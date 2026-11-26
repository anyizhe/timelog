"""通过 GitHub REST API 同步数据（不依赖 git 协议）。

为什么要有这条通道：国内访问 github.com 的 git 端口时好时坏，甚至完全连不上
（实测 clone 连续 5 次 "Failed to connect to github.com port 443"），
但 api.github.com 通常是通的。数据同步是每天都要跑的日常功能，不能靠运气。

通道对比：
    git  —— 有版本历史、能 clone 下来看；但网络不稳时要重试，冲突要处理
    api  —— 无状态、每个文件独立读写、不需要本地 git 仓库；靠重试和 sha 解决并发
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
UA = "timelog"


def _blob_sha(data: bytes) -> str:
    """算 git blob 的 sha1 —— 跟 GitHub API 返回的 sha 是同一种算法。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# ---------------------------------------------------------------- 凭据


def get_token() -> str:
    """从 Git Credential Manager 里取 GitHub token。"""
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        for line in (r.stdout or "").splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def login() -> str:
    """没有凭据就弹一次浏览器登录（Git Credential Manager 会记住）。"""
    gcm = Path(r"C:\Program Files\Git\mingw64\bin\git-credential-manager.exe")
    if not gcm.exists():
        return ""
    try:
        subprocess.run(
            [str(gcm), "github", "login", "--browser"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=300,
        )
    except Exception:
        pass
    return get_token()


# ---------------------------------------------------------------- API 封装


def call(method: str, path: str, tok: str, body: dict | None = None, timeout: int = 40):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"error": str(e)}


def call_retry(method: str, path: str, tok: str, body: dict | None = None,
               tries: int = 3, timeout: int = 40):
    """网络抽风时重试。"""
    code, data = 0, {}
    for i in range(tries):
        code, data = call(method, path, tok, body, timeout)
        if code and code not in (0, 500, 502, 503, 504):
            return code, data
        if i < tries - 1:
            time.sleep(3 * (i + 1))
    return code, data


def parse_slug(repo_url: str) -> tuple[str, str]:
    """https://github.com/owner/repo.git → (owner, repo)"""
    s = repo_url.rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"看不出仓库地址：{repo_url}")
    return parts[-2], parts[-1]


# ---------------------------------------------------------------- 内容处理


def _event_key(line: str) -> tuple:
    try:
        r = json.loads(line)
    except Exception:
        return (line,)
    return (
        round(float(r.get("t", 0)), 3),
        r.get("kind", ""),
        r.get("app", ""),
        r.get("title", ""),
        r.get("note", ""),
        r.get("m", ""),
    )


def merge_text(a: str, b: str) -> tuple[str, int]:
    """两段 jsonl 取并集（按事件身份去重），按时间排序。返回 (合并结果, 新增条数)。"""
    seen: set[tuple] = set()
    out: list[str] = []
    for text in (a, b):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except Exception:
                continue
            k = _event_key(line)
            if k in seen:
                continue
            seen.add(k)
            out.append(line)
    out.sort(key=lambda ln: json.loads(ln).get("t", 0))
    before = len(a.splitlines())
    return "\n".join(out) + ("\n" if out else ""), max(0, len(out) - before)


# ---------------------------------------------------------------- 主流程


def run(store, dry_run: bool = False) -> tuple[bool, str]:
    from sync import load_sync_config

    cfg = load_sync_config()
    repo_url = cfg.get("repo", "")
    if not repo_url:
        return False, "还没配置数据仓库。先跑：python timelog.py sync setup <仓库地址>"
    try:
        owner, repo = parse_slug(repo_url)
    except ValueError as exc:
        return False, str(exc)

    tok = get_token()
    if not tok:
        print("没有 GitHub 凭据，正在打开浏览器让你登录…")
        tok = login()
    if not tok:
        return False, (
            "拿不到 GitHub 凭据。手动登录一次：\n"
            "  git credential-manager github login --browser\n"
            "（浏览器弹出后点 Authorize 就行）"
        )

    base = f"/repos/{owner}/{repo}/contents"
    lines: list[str] = []

    def list_remote() -> dict:
        code, listing = call_retry("GET", base + "/", tok)
        if code == 404:
            # 空仓库列目录就返回 404「This repository is empty.」—— 当成空列表，不是错误
            msg = str(listing.get("message", ""))
            if "empty" in msg.lower():
                return {}
            raise RuntimeError(f"读不到远端仓库（{code}）：{msg}")
        if code != 200:
            raise RuntimeError(f"读不到远端仓库（{code}）：{listing.get('message', listing)}")
        return {x["name"]: x for x in listing if x.get("name", "").endswith(".jsonl")}

    def pull_new(remote: dict) -> int:
        """把远端版本拉回来。

        远端对**别的机器**的文件是权威的，所以本地那份不一样就要覆盖 ——
        只补"本地没有的文件"会让另一台电脑永远停在上次拉取时的旧快照
        （这坑踩过：主机上的「球球」数据卡在 00:26 不动了）。
        用远端给的 git blob sha 跟本地算的比，只在真的不一样时才下载。
        """
        got = 0
        for name, meta in remote.items():
            dst = store.data_dir / name
            if name.endswith(f".{store.machine['id']}.jsonl"):
                continue  # 自己那份走后面的合并逻辑
            rsha = meta.get("sha")
            if dst.exists() and rsha and _blob_sha(dst.read_bytes()) == rsha:
                continue  # 内容一致，不用下
            if dry_run:
                got += 1
                continue
            code, content = call_retry("GET", f"{base}/{name}", tok)
            if code == 200 and content.get("content"):
                raw = base64.b64decode(content["content"]).decode("utf-8", errors="replace")
                dst.write_text(raw, encoding="utf-8")
                got += 1
        return got

    # 1) 列远端文件
    remote = list_remote()
    lines.append(f"远端有 {len(remote)} 个数据文件")

    # 2) 拉别人的文件（本地没有才下）
    pulled = pull_new(remote)
    lines.append(f"仓库 → 本地：新拉回 {pulled} 个文件")

    # 3) 推自己的文件
    pushed = 0
    for f in sorted(store.data_dir.glob("*.jsonl")):
        local_text = f.read_text(encoding="utf-8")
        meta = remote.get(f.name)
        if meta:
            # 远端也有自己这份 → 取回并集再决定要不要传
            code, content = call_retry("GET", f"{base}/{f.name}", tok)
            if code != 200 or not content.get("content"):
                continue
            remote_text = base64.b64decode(content["content"]).decode("utf-8", errors="replace")
            merged, added = merge_text(local_text, remote_text)
            if added:
                f.write_text(merged, encoding="utf-8")
                local_text = merged
            if merged == remote_text:
                continue
            sha = content.get("sha")
        else:
            sha = None
        if dry_run:
            pushed += 1
            continue
        body = {
            "message": f"sync {dt.datetime.now():%Y-%m-%d %H:%M} · {store.machine['name']}",
            "content": base64.b64encode(local_text.encode("utf-8")).decode(),
        }
        if sha:
            body["sha"] = sha
        code, res = call_retry("PUT", f"{base}/{f.name}", tok, body)
        if code in (200, 201):
            pushed += 1
        elif code == 422:
            # sha 对不上（另一台机器刚好也在写）→ 重读一次再来
            code2, content = call_retry("GET", f"{base}/{f.name}", tok)
            if code2 == 200:
                body["sha"] = content.get("sha")
                code3, _ = call_retry("PUT", f"{base}/{f.name}", tok, body)
                if code3 in (200, 201):
                    pushed += 1
    lines.append(f"本地 → 仓库：上传 {pushed} 个文件")

    # 3.5) 再列一次：GitHub 的文件列表有延迟 —— 对方刚推上来的文件
    #      在第一次列表里可能还没出现，这里补拉一次，避免漏数据。
    if not dry_run:
        try:
            remote2 = list_remote()
            extra = pull_new(remote2)
            if extra:
                lines.append(f"补拉（列表延迟）：又拉回 {extra} 个文件")
        except RuntimeError as exc:
            lines.append(f"补拉时出了点问题（不影响已完成的部分）：{exc}")

    # 4) 重算报告
    if not dry_run:
        from report import generate_daily_report, generate_weekly_report

        days = store.available_days()
        recent = [d for d in days if d >= dt.date.today() - dt.timedelta(days=60)]
        n = 0
        for d in recent:
            try:
                generate_daily_report(store, d)
                n += 1
            except Exception:
                pass
        monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
        for k in range(4):
            try:
                generate_weekly_report(store, monday - dt.timedelta(days=7 * k))
            except Exception:
                pass
        lines.append(f"重新生成报告：{n} 份日报")

    cfg["last_sync"] = dt.datetime.now().isoformat(timespec="seconds")
    from sync import save_sync_config

    save_sync_config(cfg)
    lines.append("同步完成 ✓")
    return True, "\n".join(lines)
