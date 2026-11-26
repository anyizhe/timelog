"""一键把 timelog 代码发布到 GitHub（公开仓库）。

用法：
    python publish.py                 # 交互式：问你要仓库地址
    python publish.py --repo <url>    # 直接给地址
    python publish.py --no-push       # 只提交不推送

第一次推送时 Git Credential Manager 会弹出浏览器让你登录 GitHub，登录一次就记住了。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_NAME = "timelog"
DEFAULT_DESC = "本地时间记录工具：后台记录窗口活动，生成每日/每周报告，带网页看板"


def git(*args: str, cwd: Path = BASE_DIR) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "找不到 git，请先安装 Git for Windows"


def ensure_identity() -> bool:
    """确保这个仓库有提交身份。

    只写**仓库本地**配置，不动用户的全局 git config —— 工具不该替用户改全局设置。
    """
    code, _ = git("config", "user.name")
    if code != 0 or not git("config", "user.name")[1].strip():
        print("还没配 git 身份。GitHub 上要用你账号的邮箱（否则提交关联不上你的账号）。")
        name = email = ""
        try:
            name = input("  你的名字（比如 chen）: ").strip()
            email = input("  你的 GitHub 邮箱: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
        if not name or not email:
            name, email = "timelog", "timelog@localhost"
            print("  用兜底身份提交（timelog <timelog@localhost>）。")
            print("  想让提交显示你的 GitHub 昵称，之后可以跑：")
            print("    git config --global user.name 你的名字")
            print("    git config --global user.email 你的邮箱")
        git("config", "user.name", name)
        git("config", "user.email", email)
        print(f"  已设置（仅本仓库）：{name} <{email}>")
    return True


# ---------------------------------------------------------------- API 兜底通道


def _token() -> str:
    payload = "protocol=https\nhost=github.com\nusername=anyizhe\n\n"
    r = subprocess.run(
        ["git", "credential", "fill"], input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    for line in (r.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def _api(method: str, path: str, tok: str, body: dict | None = None):
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("User-Agent", "timelog")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}


def push_via_api(repo_name: str = "timelog") -> tuple[bool, str]:
    """git 端口不通时的兜底：直接用 REST API 传文件。

    每次 PUT 一个文件就是一个 commit，所以版本历史照样有。
    """
    import base64
    import hashlib

    tok = _token()
    if not tok:
        return False, "没有 GitHub 凭据"
    code, me = _api("GET", "/user", tok)
    if code != 200:
        return False, f"认证失败 {code}"
    owner = me.get("login", "anyizhe")

    files = [
        "timelog.py", "collector.py", "store.py", "report.py", "config.py", "config.example.json",
        "web.py", "machine.py", "sync.py", "sync_api.py", "publish.py", "setup.py",
        "shortcut.py", "join.py",
        "ui/index.html",
        "tests/selftest.py", "tests/selftest_sync.py", "tests/selftest_api.py",
        "tests/selftest_join.py",
        "README.md", "clock.ico", ".gitignore", "config.example.json",
        "start.bat", "stop.bat", "report.bat", "open-web.bat", "autostart.bat",
        "sync.bat", "publish.bat", "setup.bat", "update.bat",
    ]

    def blob_sha(data: bytes) -> str:
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    code, listing = _api("GET", f"/repos/{owner}/{repo_name}/git/trees/main?recursive=1", tok)
    remote = {}
    if code == 200:
        for item in listing.get("tree", []):
            if item.get("type") == "blob":
                remote[item["path"]] = item.get("sha")

    ok = 0
    failed: list[str] = []
    for name in files:
        p = BASE_DIR / name
        if not p.exists():
            continue
        data = p.read_bytes()
        if remote.get(name) == blob_sha(data):
            continue
        body = {
            "message": f"publish: {name}",
            "content": base64.b64encode(data).decode(),
            "branch": "main",
        }
        if name in remote:
            body["sha"] = remote[name]
        code, res = _api("PUT", f"/repos/{owner}/{repo_name}/contents/{name}", tok, body)
        if code in (200, 201):
            ok += 1
        else:
            failed.append(f"{name}({code})")
    return (not failed), f"API 通道：更新 {ok} 个文件" + (
        f"，失败：{', '.join(failed)}" if failed else ""
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="把 timelog 发布到 GitHub")
    ap.add_argument("--repo", help="GitHub 仓库地址（https://github.com/用户名/仓库.git）")
    ap.add_argument("--api", action="store_true", help="直接用 REST API 上传（git 端口不通时用）")
    ap.add_argument("--no-push", action="store_true", help="只本地提交，不推送")
    ap.add_argument("--message", default="", help="提交信息")
    args = ap.parse_args()

    print("=" * 58)
    print(" 发布 timelog 到 GitHub")
    print("=" * 58)

    code, out = git("--version")
    if code != 0:
        print("没找到 git。先装 Git for Windows：https://git-scm.com/download/win")
        return 1

    if not (BASE_DIR / ".git").exists():
        git("init", "-b", "main")
        print("· 已初始化 git 仓库（main 分支）")
    else:
        git("symbolic-ref", "HEAD", "refs/heads/main")

    ensure_identity()

    git("add", "-A")
    code, out = git("commit", "-m", args.message or "timelog: 时间记录工具")
    if code != 0:
        if "nothing to commit" in out.lower():
            print("· 没有需要提交的新改动")
        else:
            print("提交失败：\n" + out.strip())
            return 1
    else:
        print("· 已提交")

    if args.no_push:
        print("\n已跳过推送。下次跑：python publish.py")
        return 0

    code, remote = git("remote", "get-url", "origin")
    url = args.repo or (remote.strip() if code == 0 else "")
    if not url:
        print()
        print("还没设置远端仓库。请按下面做：")
        print()
        print(f"  1) 打开这个页面建一个**公开**仓库（名字建议 {DEFAULT_NAME}，勾上 README 也可以）：")
        print(f"     https://github.com/new?name={DEFAULT_NAME}&description=see-also")
        print()
        print("  2) 把建好后地址栏里的 https://github.com/你的用户名/timelog.git 粘贴进来")
        print()
        try:
            url = input("  粘贴仓库地址（直接回车跳过）: ").strip()
        except EOFError:
            url = ""
        if not url:
            print("\n没有地址，本次到此为止。代码已经本地提交好了，随时可以再跑 publish.py。")
            return 0

    if code == 0 and remote.strip():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)
    print(f"· 远端：{url}")

    print()
    print("正在推送……第一次会弹出浏览器让你登录 GitHub，登录一次就好。")
    code, out = 1, ""
    for attempt in range(3):
        code, out = git("push", "-u", "origin", "main")
        if code == 0:
            break
        last = out.strip().splitlines()[-1] if out.strip() else ""
        print(f"  第 {attempt + 1} 次失败：{last[:110]}")
        if attempt < 2:
            import time

            time.sleep(5 * (attempt + 1))
    if code != 0:
        print("  git 端口不通，改用 API 通道再试一次……")
        ok, msg = push_via_api()
        print(("OK " if ok else "FAIL ") + msg)
        if not ok:
            print("推送失败：\n" + out.strip())
            print()
            print("常见原因：")
            print("  · 仓库不存在或名字不对 —— 先在 GitHub 上建一个")
            print("  · 仓库不是空的 —— 建的时候不要勾 README")
            print("  · 登录过期 —— 重跑一次，会重新弹登录")
            return 1

    print()
    print("推送成功 ✓")
    print()
    print("你的代码仓库：")
    print(f"  {url}")
    print()
    print("在另一台电脑上装：")
    print(f"  git clone {url}")
    print("  cd timelog && python setup.py")
    print()
    print("提示：代码仓可以公开，但**活动数据仓库（timelog-data）请务必设为私有**，")
    print("      里面的窗口标题可能含敏感信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
