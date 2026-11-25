"""跨机器数据同步：GitHub 私有数据仓。

思路：
  - 数据文件名带机器号（`2026-11-25.a3f9c2.jsonl`），两台电脑推同一个仓库
    **永远不会撞文件名**，git 不需要任何冲突处理。
  - 本地 `data/` 目录是"工作区"，数据仓库是"中转站"。
    sync 做三件事：本地 → 仓库 → 拉回别人机器的文件 → 重算报告 → 提交推送。

命令：
    python timelog.py sync setup <仓库地址>   首次配置
    python timelog.py sync run                同步一次（拉取 + 推送 + 重算报告）
    python timelog.py sync status             看状态
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _sync_file() -> Path:
    # 环境变量可覆盖（自测要模拟两台机器各自独立的同步配置）
    env = os.environ.get("TIMELOG_SYNC_FILE")
    return Path(env) if env else BASE_DIR / "sync.json"


DEFAULT_REPO_DIR = BASE_DIR.parent / "timelog-data"

# 只同步数据文件，pid/log/flag 这些运行时文件留在本地
DATA_GLOB = "*.jsonl"


# ---------------------------------------------------------------- 配置


def load_sync_config() -> dict:
    p = _sync_file()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_sync_config(cfg: dict) -> Path:
    p = _sync_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def repo_dir() -> Path:
    cfg = load_sync_config()
    p = cfg.get("path")
    return Path(p) if p else DEFAULT_REPO_DIR


# ---------------------------------------------------------------- git 封装


def git(repo: Path, *args: str, check: bool = False) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (r.stdout or "") + (r.stderr or "")
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失败：\n{out.strip()}")
        return r.returncode, out
    except FileNotFoundError:
        return 127, "找不到 git，请先安装 Git for Windows"


def git_ok(repo: Path, *args: str) -> bool:
    code, _ = git(repo, *args)
    return code == 0


# ---------------------------------------------------------------- 事件合并


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


def merge_jsonl(local: Path, remote: Path) -> int:
    """把 remote 里本地没有的事件并进 local（按事件身份去重）。返回新增条数。"""
    seen: set[tuple] = set()
    out_lines: list[str] = []
    for p in (local, remote):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            k = _event_key(line)
            if k in seen:
                continue
            seen.add(k)
            out_lines.append(line)
    out_lines.sort(key=lambda ln: json.loads(ln).get("t", 0))
    before = len(local.read_text(encoding="utf-8").splitlines()) if local.exists() else 0
    local.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    return len(out_lines) - before


# ---------------------------------------------------------------- 主流程


def setup(url: str | None = None, repo_path: str | None = None) -> tuple[bool, str]:
    cfg = load_sync_config()
    if url:
        cfg["repo"] = url.strip()
    if repo_path:
        cfg["path"] = str(Path(repo_path).expanduser().resolve())
    repo = repo_dir()
    cfg["path"] = str(repo)
    save_sync_config(cfg)

    if not cfg.get("repo"):
        return False, (
            "还没指定仓库地址。\n"
            "  1) 在 GitHub 上新建一个 **Private** 仓库（不要勾 README/LICENSE），名字随便，比如 timelog-data\n"
            "  2) 把它的地址发我，或自己跑：\n"
            "     python timelog.py sync setup https://github.com/<你的用户名>/timelog-data.git"
        )

    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        code, out = git(repo, "init", "-b", "main")
        if code != 0:
            code, out = git(repo, "init")
            git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    git_ok(repo, "symbolic-ref", "HEAD", "refs/heads/main")

    # 数据仓库只要能提交就行，不强制用 GitHub 身份（代码仓才需要）
    code, name = git(repo, "config", "user.name")
    if code != 0 or not name.strip():
        git(repo, "config", "user.name", "timelog")
        git(repo, "config", "user.email", "timelog@localhost")
        print("· 数据仓库用本地身份提交（timelog <timelog@localhost>）")

    # 注意：不能用 `git remote` 判断远端是否存在 —— 没有远端时它也返回 0 且输出为空
    code, _ = git(repo, "remote", "get-url", "origin")
    if code == 0:
        git(repo, "remote", "set-url", "origin", cfg["repo"])
    else:
        git(repo, "remote", "add", "origin", cfg["repo"])

    keep = repo / ".gitignore"
    if not keep.exists():
        keep.write_text("*\n!.gitignore\n!README.md\n*.jsonl\n", encoding="utf-8")
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# timelog-data\n\n"
            "这个仓库是 timelog 的**数据仓库**，存放各台电脑的活动记录。\n\n"
            "- 每个文件是一天的数据，文件名带机器号（`2026-11-25.a3f9c2.jsonl`）\n"
            "- 里面的内容包含窗口标题，**请保持仓库私有**\n"
            "- 由 `timelog.py sync` 自动维护，手动改容易冲突\n",
            encoding="utf-8",
        )

    code, out = git(repo, "remote", "-v")
    return True, (
        f"数据仓库已就绪：{repo}\n"
        f"远端：{cfg['repo']}\n\n"
        "下一步：\n"
        "  1) 确认 GitHub 上已经建好这个**私有**仓库（空的，没有 README）\n"
        "  2) 跑一次首次推送（会在浏览器里让你登录 GitHub）：\n"
        "     python timelog.py sync run\n\n"
        "两台电脑都跑一次 sync setup + sync run，之后每天 sync run 一次即可。"
    )


def run(store, dry_run: bool = False) -> tuple[bool, str]:
    cfg = load_sync_config()
    if not cfg.get("repo"):
        return False, "还没配置数据仓库。先跑：python timelog.py sync setup <仓库地址>"
    repo = repo_dir()
    if not (repo / ".git").exists():
        return False, f"本地数据仓库不存在：{repo}\n先跑：python timelog.py sync setup <仓库地址>"

    lines: list[str] = []
    mine_id = store.machine["id"]

    # 1) 本地 → 仓库
    local_files = sorted(store.data_dir.glob(DATA_GLOB))
    copied = 0
    for f in local_files:
        dst = repo / f.name
        if not dst.exists() or dst.read_bytes() != f.read_bytes():
            if not dry_run:
                shutil.copy2(f, dst)
            copied += 1
    lines.append(f"本地 → 仓库：{copied} 个文件有变化")

    # 2) 提交本地改动
    if not dry_run:
        git(repo, "add", "-A")
        code, out = git(repo, "commit", "-m", f"sync {dt.datetime.now():%Y-%m-%d %H:%M}")
        if code != 0 and "nothing to commit" not in out.lower():
            lines.append(f"提交时有点小问题（可忽略）：{out.strip().splitlines()[-1] if out.strip() else ''}")

    # 3) 拉取别人的数据
    code, out = git(repo, "pull", "--rebase", "--autostash")
    pulled = "Already up to date" in out
    lines.append(f"拉取远端：{'没有新东西' if pulled else '拉到更新'}")

    # 4) 仓库 → 本地（自己那份做并集合并，不丢数据）
    received = 0
    for f in sorted(repo.glob(DATA_GLOB)):
        dst = store.data_dir / f.name
        if f.name.endswith(f".{mine_id}.jsonl"):
            if dst.exists() and not dry_run:
                received += merge_jsonl(dst, f)
            elif not dst.exists() and not dry_run:
                shutil.copy2(f, dst)
                received += 1
        else:
            if not dst.exists() or dst.read_bytes() != f.read_bytes():
                if not dry_run:
                    shutil.copy2(f, dst)
                received += 1
    lines.append(f"仓库 → 本地：{received} 处更新")

    # 5) 重算受影响的报告
    days = store.available_days()
    recent = [d for d in days if d >= dt.date.today() - dt.timedelta(days=60)]
    regen = 0
    if not dry_run:
        from report import generate_daily_report, generate_weekly_report

        for d in recent:
            try:
                generate_daily_report(store, d)
                regen += 1
            except Exception:
                pass
        monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
        for k in range(4):
            try:
                generate_weekly_report(store, monday - dt.timedelta(days=7 * k))
            except Exception:
                pass
    lines.append(f"重新生成报告：{regen} 份日报")

    # 6) 推送
    if dry_run:
        lines.append("（--dry-run：没有真的提交和推送）")
        return True, "\n".join(lines)

    git(repo, "add", "-A")
    code, out = git(repo, "commit", "-m", f"merge {dt.datetime.now():%Y-%m-%d %H:%M}")
    if code != 0 and "nothing to commit" not in out.lower():
        lines.append(f"提交时有点小问题：{out.strip().splitlines()[-1] if out.strip() else ''}")
    code, out = git(repo, "push", "-u", "origin", "main")
    if code != 0:
        msg = out.strip()
        if "could not read Username" in msg or "Authentication failed" in msg or "403" in msg:
            lines.append(
                "推送失败：还没登录 GitHub。\n"
                "  浏览器会弹出登录页，登录一次就好（Git Credential Manager 会记住）。\n"
                f"  详细信息：{msg.splitlines()[-1] if msg else ''}"
            )
        else:
            lines.append(f"推送失败：{msg.splitlines()[-1] if msg else '未知错误'}")
        return False, "\n".join(lines)
    lines.append("推送：完成 ✓")

    cfg["last_sync"] = dt.datetime.now().isoformat(timespec="seconds")
    save_sync_config(cfg)
    return True, "\n".join(lines)


def status(store) -> str:
    cfg = load_sync_config()
    repo = repo_dir()
    lines = [
        f"本机：{store.machine['name']}（id {store.machine['id']}）",
        f"数据目录：{store.data_dir}",
        f"数据仓库：{repo}" + ("" if (repo / ".git").exists() else "　（还没初始化）"),
        f"远端：{cfg.get('repo') or '（未配置）'}",
        f"上次同步：{cfg.get('last_sync') or '（还没同步过）'}",
        "",
    ]
    days = store.available_days()
    if not days:
        lines.append("还没有任何数据。")
        return "\n".join(lines)
    lines.append("各机器的数据：")
    by_machine: dict[str, list[str]] = {}
    for f in sorted(store.data_dir.glob(DATA_GLOB)):
        parts = f.stem.split(".")
        name = parts[1] if len(parts) > 1 else "(旧格式)"
        by_machine.setdefault(name, []).append(f.name)
    for name, files in sorted(by_machine.items()):
        lines.append(f"  {name}：{len(files)} 天（{files[0].split('.')[0]} ~ {files[-1].split('.')[0]}）")
    return "\n".join(lines)
