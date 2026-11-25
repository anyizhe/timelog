"""新电脑第一次安装时的引导。

    git clone <仓库地址>
    cd timelog
    python setup.py

会依次：检查环境 → 给这台机器起名 → 问要不要装开机自启 → 问要不要配数据同步。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import ensure_config_file  # noqa: E402
from machine import load_machine  # noqa: E402
from store import Store  # noqa: E402


def ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def yes(prompt: str, default: bool = False) -> bool:
    ans = ask(prompt + " [y/N]: ")
    if not ans:
        return default
    return ans.lower() in ("y", "yes", "是", "好")


def main() -> int:
    print("=" * 58)
    print(" timelog 安装引导")
    print("=" * 58)

    if sys.version_info < (3, 8):
        print(f"Python 版本太老（{sys.version.split()[0]}），需要 3.8 以上。")
        return 1
    print(f"· Python {sys.version.split()[0]}  OK")

    ensure_config_file()
    store = Store()
    info = load_machine()
    print(f"· 数据目录 {store.data_dir}")
    print(f"· 报告目录 {store.report_dir}")

    # 1. 起名
    print()
    print("【1/4】给这台机器起个名字")
    print(f"    现在叫「{info['name']}」，id {info['id']}")
    name = ask("    改成什么？（比如 家里台式机 / 公司笔记本，直接回车不改）: ")
    if name and name != info["name"]:
        from machine import rename

        rename(name)
        print(f"    好，现在是「{name}」")

    # 2. 桌面快捷方式
    print()
    print("【2/4】桌面快捷方式")
    from shortcut import create_desktop_shortcut

    ok, msg = create_desktop_shortcut()
    if ok:
        print(f"    已建好：{msg}")
    else:
        print(f"    没建成（{msg}），以后可以手动跑 python shortcut.py")

    # 3. 开机自启
    print()
    print("【3/4】要不要开机自动开始记录？")
    if yes("    装上开机自启吗", default=True):
        import winreg

        cmdline = (
            f'"{sys.executable}" "{BASE_DIR / "timelog.py"}" start --bg'
        )
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_ALL_ACCESS,
            )
            with key:
                winreg.SetValueEx(key, "TimelogCollector", 0, winreg.REG_SZ, cmdline)
            print("    已装上（用 python.exe，会闪一下黑框；想无窗口请改用 pythonw）")
        except Exception as exc:
            print(f"    没装成功：{exc}\n    可以手动跑：python timelog.py autostart install")
    else:
        print("    跳过。想装随时跑：python timelog.py autostart install")

    # 4. 数据同步
    print()
    print("【4/4】要不要和另一台电脑合并数据？")
    if yes("    配置数据同步吗（需要一个 GitHub 私有仓库）", default=False):
        print()
        print("    先在 GitHub 上新建一个**私有**空仓库（名字比如 timelog-data，")
        print("    不要勾 README），然后回来跑：")
        print()
        print("      python timelog.py sync setup https://github.com/你的用户名/timelog-data.git")
        print("      python timelog.py sync run")
        print()
        print("    两台电脑都跑一次 sync run，之后每天跑一次就会自动合并。")
    else:
        print("    跳过。想配随时跑：python timelog.py sync setup <仓库地址>")

    print()
    print("=" * 58)
    print(" 装好了。接下来：")
    print("   python timelog.py start --bg    开始后台记录")
    print("   python timelog.py web           打开网页看板")
    print("   桌面快捷方式（如果已建）：双击「时间记录看板」")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
