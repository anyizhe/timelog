"""新电脑一键接入：拉数据、起名、装自动同步、建桌面快捷方式。

在另一台电脑上：
    git clone https://github.com/anyizhe/timelog.git
    cd timelog
    python join.py --name 笔记本

只需要这几步，剩下全自动。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# 配好要接的数据仓库（新建时自动写进 sync.json）
DEFAULT_DATA_REPO = "https://github.com/anyizhe/timelog-data.git"


def sh(*args: str, cwd: Path = BASE_DIR) -> tuple[int, str]:
    try:
        r = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=180,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, f"找不到 {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"{args[0]} 超时"


def main() -> int:
    ap = argparse.ArgumentParser(description="把 timelog 接入这台电脑")
    ap.add_argument("--name", default="", help="给这台机器起个名字，比如 公司笔记本")
    ap.add_argument("--data-repo", default=DEFAULT_DATA_REPO, help="数据仓库地址")
    ap.add_argument("--minutes", type=int, default=30, help="自动同步间隔（分钟）")
    ap.add_argument("--no-autostart", action="store_true", help="不装自动同步")
    args = ap.parse_args()

    print("=" * 58)
    print(" timelog 接入这台电脑")
    print("=" * 58)

    if sys.version_info < (3, 8):
        print(f"Python 版本太老（{sys.version.split()[0]}），需要 3.8 以上。")
        print("去 https://www.python.org/downloads/ 装一个。")
        return 1
    print(f"· Python {sys.version.split()[0]}  OK")

    # 沙箱模式（自测用）：设了机器身份文件就说明不是正式安装，
    # 这时候绝不碰桌面快捷方式和系统计划任务
    sandbox = bool(os.environ.get("TIMELOG_MACHINE_FILE"))

    code, out = sh("git", "--version")
    if code != 0:
        print("没找到 git。装 Git for Windows：https://git-scm.com/download/win")
        return 1
    print(f"· {out.strip()}")

    # ---- 1. 机器身份 ----
    from machine import load_machine, rename

    info = load_machine()
    name = args.name.strip() or info["name"]
    if name != info["name"]:
        info = rename(name)
    print(f"· 这台机器：{info['name']}（id {info['id']}）")

    # ---- 2. 桌面快捷方式 ----
    from shortcut import create_desktop_shortcut

    if sandbox:
        print("· 桌面快捷方式：沙箱模式，跳过")
    else:
        ok, msg = create_desktop_shortcut()
        print(f"· 桌面快捷方式：{'已建 ' + msg if ok else '没建成（' + msg + '）'}")

    # ---- 3. 接数据仓库 ----
    import sync

    ok, msg = sync.setup(args.data_repo, str(BASE_DIR.parent / "timelog-data"))
    print("· 数据仓库：" + ("已配置" if ok else "配置失败"))
    if not ok:
        print(msg)

    from store import Store

    store = Store()
    ok, msg = sync.run(store)
    print("· 首次同步：" + ("成功" if ok else "失败"))
    if not ok:
        print(msg)
    else:
        print("   " + msg.replace("\n", "\n   "))

    # ---- 4. 自动同步 ----
    if sandbox:
        print("· 自动同步：沙箱模式，跳过")
    elif not args.no_autostart:
        ok, msg = sync.install_task(minutes=args.minutes)
        print("· 自动同步：" + ("已装" if ok else "没装上"))
        if not ok:
            print(msg)

    # ---- 5. 开机自启 ----
    if sandbox:
        print("· 开机自动记录：沙箱模式，跳过")
    else:
        code, out = sh(sys.executable, str(BASE_DIR / "timelog.py"), "autostart", "install")
        print("· 开机自动记录：" + ("已装" if code == 0 else "没装上，可手动跑 start.bat"))

    # ---- 6. 立刻开始记录 ----
    # 只注册开机自启是不够的：不手动启动的话，这台电脑当场什么都不记，
    # 要等下次重启才生效（这坑踩过：新机器接入后半天没数据）。
    if sandbox:
        print("· 采集器：沙箱模式，跳过")
    else:
        code, out = sh(sys.executable, str(BASE_DIR / "timelog.py"), "start", "--bg")
        started = code == 0
        print("· 采集器：" + ("已经在记了" if started else "没启动成功"))
        if not started:
            print("  " + (out.strip().splitlines()[-1] if out.strip() else ""))

        # ---- 7. 启动后再同步一次，把刚记上的数据推上去 ----
        if started:
            ok2, msg2 = sync.run(store)
            print("· 启动后同步：" + ("成功" if ok2 else "失败"))
            if not ok2:
                print("  " + msg2.replace("\n", "\n  "))

    print()
    print("=" * 58)
    print(" 接入完成。接下来：")
    print("   python timelog.py start --bg    开始记录（已在跑就忽略）")
    print("   python timelog.py status       看今天记了多少")
    print("   桌面双击「时间记录看板」           看今天干了啥")
    print("=" * 58)
    print()
    print("另一台电脑上也要跑一次同样的接入命令，数据就会自动互相同步了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
