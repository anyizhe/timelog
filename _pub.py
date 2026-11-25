"""发布代码仓到 GitHub（公开）+ 可选创建数据仓（私有）。

    python publish_repo.py                 # 交互：问要不要建数据仓
    python publish_repo.py --yes           # 全部按推荐来，不问
    python publish_repo.py --code-only     # 只发代码仓
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(r"C:\Users\13671\timelog")
CODE_REPO = "timelog"
DATA_REPO = "timelog-data"
USER = "anyizhe"


def get_token() -> str:
    payload = "protocol=https\nhost=github.com\nusername=%s\n\n" % USER
    r = subprocess.run(
        ["git", "credential", "fill"], input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
    )
    for line in (r.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def api(method: str, path: str, tok: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("User-Agent", "timelog")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:400]}


def git(*args: str, cwd: Path = BASE) -> tuple[int, str]:
    r = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=300,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="不问，直接按推荐做")
    ap.add_argument("--code-only", action="store_true", help="只发布代码仓")
    args = ap.parse_args()

    tok = get_token()
    if not tok:
        print("取不到 GitHub 凭据。跑一次：git credential-manager github login --browser")
        return 1
    code, me = api("GET", "/user", tok)
    if code != 0:
        print(f"认证失败: {me}")
        return 1
    login = me.get("login", USER)
    print(f"✓ 认证：{login}")

    # ---- 代码仓（公开）----
    code, repo = api("GET", f"/repos/{login}/{CODE_REPO}", tok)
    if code == 200:
        print(f"· 代码仓已存在：{repo['full_name']}　private={repo['private']}")
    else:
        code, repo = api("POST", "/user/repos", tok, {
            "name": CODE_REPO,
            "private": False,
            "description": "本地时间记录工具：后台记录窗口活动，生成每日/每周报告，带网页看板与多机同步",
            "auto_init": False,
        })
        if code != 201:
            print(f"创建代码仓失败: {code} {repo}")
            return 1
        print(f"✓ 已创建代码仓（公开）：{repo['full_name']}")

    # ---- 数据仓（私有）----
    if not args.code_only:
        code, drepo = api("GET", f"/repos/{login}/{DATA_REPO}", tok)
        if code == 200:
            print(f"· 数据仓已存在：{drepo['full_name']}　private={drepo['private']}")
        else:
            code, drepo = api("POST", "/user/repos", tok, {
                "name": DATA_REPO,
                "private": True,
                "description": "timelog 的活动数据（私有：内含窗口标题）",
                "auto_init": False,
            })
            if code != 201:
                print(f"创建数据仓失败: {code} {drepo}")
            else:
                print(f"✓ 已创建数据仓（私有）：{drepo['full_name']}")

    # ---- 推送代码 ----
    url = f"https://github.com/{login}/{CODE_REPO}.git"
    code, remote = git("remote", "get-url", "origin")
    if code == 0 and remote.strip():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)

    git("add", "-A")
    code, out = git("commit", "-m", "timelog: 时间记录工具（网页看板 + 多机同步 + 一键发布）")
    if code != 0 and "nothing to commit" not in out.lower():
        print("提交失败：\n" + out.strip()[:400])
        return 1
    print("· 已提交")

    print("正在推送代码仓（网络不稳可能会重试）……")
    code, out = git("push", "-u", "origin", "main")
    if code != 0:
        print("推送失败：\n" + out.strip()[-600:])
        return 1
    print(f"✓ 推送成功：{url}")
    print()
    print("另一台电脑上：")
    print(f"  git clone {url}")
    print(f"  cd {CODE_REPO}")
    print("  python join.py --name 笔记本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
