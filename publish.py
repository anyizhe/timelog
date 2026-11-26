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
            encoding="utf-8", errors="replace",
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


def main() -> int:
    ap = argparse.ArgumentParser(description="把 timelog 发布到 GitHub")
    ap.add_argument("--repo", help="GitHub 仓库地址（https://github.com/用户名/仓库.git）")
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
    for attempt in range(4):
        code, out = git("push", "-u", "origin", "main")
        if code == 0:
            break
        last = out.strip().splitlines()[-1] if out.strip() else ""
        print(f"  第 {attempt + 1} 次失败：{last[:110]}")
        if attempt < 3:
            import time

            time.sleep(5 * (attempt + 1))
    if code != 0:
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
