#!/usr/bin/env python3
"""timelog —— 后台时间记录 + 手动补充 + 每日/每周报告。

用法速查：
    python timelog.py start              前台启动监控（Ctrl+C 停止）
    python timelog.py start --bg         后台启动（无窗口）
    python timelog.py stop               停止后台监控
    python timelog.py status             看当前状态与今日概况
    python timelog.py add "开需求评审"    手动记一件事
    python timelog.py add "写方案" --dur 30m
    python timelog.py add "读论文" --at 21:30 --dur 25m
    python timelog.py report daily        生成今天的日报
    python timelog.py report weekly       生成本周周报
    python timelog.py autostart install  装开机自启（remove 卸载）
    python timelog.py config              查看当前配置
    python timelog.py doctor              自检（是否拿到窗口标题等）
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import CONFIG_PATH, ensure_config_file, load_config, save_config  # noqa: E402
from store import Store  # noqa: E402

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "TimelogCollector"


# ---------------------------------------------------------------- 工具函数


def pid_alive(pid: int) -> bool:
    """进程是否还活着。Windows 上不能用 os.kill(pid,0)（会真杀进程）。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return bool(ok) and code.value == STILL_ACTIVE


def read_pid(store: Store) -> int | None:
    try:
        return int(store.pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def parse_duration(text: str | None) -> int | None:
    """'30m' / '1h' / '1h30m' / '90'(默认分钟) → 秒。"""
    if not text:
        return None
    text = text.strip().lower()
    if re.fullmatch(r"\d+", text):
        return int(text) * 60
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
    if not m or not any(m.groups()):
        raise ValueError(f"看不懂的时长格式: {text}（试试 30m / 1h / 1h30m）")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def parse_when(text: str | None) -> dt.datetime:
    """'HH:MM' / 'MM-DD HH:MM' / 'YYYY-MM-DD HH:MM' → 时间。默认现在。

    手动解析而不是 strptime：%m-%d 这种「没有年份」的格式在 Python 3.15 会改行为。
    """
    if not text:
        return dt.datetime.now().replace(microsecond=0)
    text = text.strip()
    now = dt.datetime.now()

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        return dt.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))

    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        mo, d, hh, mm, ss = m.groups()
        return dt.datetime(now.year, int(mo), int(d), int(hh), int(mm), int(ss or 0))

    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        hh, mm, ss = m.groups()
        return dt.datetime(now.year, now.month, now.day, int(hh), int(mm), int(ss or 0))

    raise ValueError(f"看不懂的时间格式: {text}（试试 21:30 或 2026-11-25 21:30）")


def pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


# ---------------------------------------------------------------- 命令


def do_start(store: Store, cfg: dict, bg: bool = True) -> tuple[bool, str]:
    """后台启动采集器。返回 (是否成功, 说明)。网页 API 也用这个。"""
    pid = read_pid(store)
    if pid and pid_alive(pid):
        return False, f"监控已经在运行了（PID {pid}），不用重复启动。"
    store.clear_stop_flag()
    if not bg:
        return False, "后台模式调用需要 bg=True"

    log = open(store.log_file, "a", encoding="utf-8")
    flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    try:
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "timelog.py"), "start"],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
            cwd=str(BASE_DIR),
        )
    finally:
        log.close()
    for _ in range(40):  # 等它把 pid 写出来
        time.sleep(0.25)
        p = read_pid(store)
        if p and pid_alive(p):
            return True, f"监控已在后台启动（PID {p}）。"
    return False, f"启动失败，看看日志：{store.log_file}"


def do_stop(store: Store) -> tuple[bool, str]:
    """停止采集器。返回 (是否成功, 说明)。网页 API 也用这个。"""
    pid = read_pid(store)
    if not pid or not pid_alive(pid):
        if store.pid_file.exists():
            try:
                store.pid_file.unlink()
            except OSError:
                pass
        store.clear_stop_flag()
        return True, "监控当前没有在运行。"
    store.stop_flag.write_text("stop", encoding="utf-8")
    for _ in range(40):  # 最多等 10 秒
        time.sleep(0.25)
        if not pid_alive(pid):
            store.clear_stop_flag()
            return True, f"监控已停止（PID {pid}）。"
    # 不听话就强杀
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    store.clear_stop_flag()
    return True, "监控已强制停止。"


def cmd_start(args) -> int:
    cfg = load_config()
    store = Store()
    ensure_config_file()

    pid = read_pid(store)
    if pid and pid_alive(pid):
        print(f"监控已经在运行了（PID {pid}），不需要重复启动。")
        print(f"停止：python timelog.py stop")
        return 1

    if args.bg:
        ok, msg = do_start(store, cfg, bg=True)
        print(msg)
        if ok:
            print(f"数据目录：{store.data_dir}")
            print("停止：python timelog.py stop")
        return 0 if ok else 1

    # 前台运行
    from collector import Collector

    store.clear_stop_flag()
    store.pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        Collector(store, cfg).run()
    finally:
        try:
            if store.pid_file.exists() and store.pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                store.pid_file.unlink()
        except OSError:
            pass
        if cfg.get("report_on_stop", True):
            try:
                from report import generate_daily_report

                path = generate_daily_report(store, dt.date.today())
                print(f"今日报告已生成：{path}")
            except Exception as exc:
                print(f"生成报告失败：{exc}")
    return 0


def cmd_stop(args) -> int:
    store = Store()
    ok, msg = do_stop(store)
    print(msg)
    return 0 if ok else 1


def cmd_web(args) -> int:
    """启动本地网页看板。"""
    import web as web_mod

    argv = []
    if args.port:
        argv += ["--port", str(args.port)]
    if args.no_browser:
        argv.append("--no-browser")
    return web_mod.main(argv)


def cmd_sync(args) -> int:
    """跨机器数据同步。"""
    import sync

    store = Store()
    if args.action == "setup":
        ok, msg = sync.setup(args.url, args.path)
        print(msg)
        return 0 if ok else 1
    if args.action == "run":
        ok, msg = sync.run(store, dry_run=getattr(args, "dry_run", False))
        print(msg)
        return 0 if ok else 1
    print(sync.status(store))
    return 0


def cmd_machine(args) -> int:
    """查看 / 修改本机身份。"""
    from machine import load_machine, rename

    if getattr(args, "name", None):
        info = rename(args.name)
        print(f"本机名称已改为：{info['name']}（id {info['id']}）")
        print("改名只影响之后产生的记录，历史记录里的旧名字不变。")
        return 0
    info = load_machine()
    print(f"本机名称：{info['name']}")
    print(f"机器 id ：{info['id']}")
    print()
    print("改名：python timelog.py machine --name 台式机")
    return 0




def cmd_status(args) -> int:
    from report import segments_for_range, summarize

    cfg = load_config()
    store = Store()
    pid = read_pid(store)
    running = bool(pid and pid_alive(pid))
    print(f"监控状态：{'运行中' if running else '未运行'}" + (f"（PID {pid}）" if running else ""))
    print(f"本机：{store.machine['name']}（id {store.machine['id']}）")
    print(f"数据目录：{store.data_dir}")
    print(f"报告目录：{store.report_dir}")
    print(f"配置文件：{CONFIG_PATH}")
    print(f"空闲判定：{cfg.get('idle_seconds')} 秒　采样间隔：{cfg.get('poll_seconds')} 秒")

    today = dt.date.today()
    segs = segments_for_range(store, today, today)
    s = summarize(segs)
    print(f"\n今日：活跃 {s['window']/3600:.1f} 小时　空闲 {s['idle']/3600:.1f} 小时　应用 {len(s['apps'])} 个")
    if s["apps"]:
        top = sorted(s["apps"].items(), key=lambda kv: -kv[1])[:5]
        print("  Top：" + "　".join(f"{a} {d/60:.0f}分" for a, d in top))
    days = store.available_days()
    if days:
        print(f"已有数据：{len(days)} 天（{days[0]} ~ {days[-1]}）")
    else:
        print("还没有任何采集数据。")
    return 0


def cmd_add(args) -> int:
    store = Store()
    note = args.note.strip()
    if not note:
        print("要记点什么？例如：python timelog.py add \"开需求评审\" --dur 45m")
        return 1
    when = parse_when(args.at)
    dur = parse_duration(args.dur)
    rec = store.append_manual(note, when, dur)
    extra = f"，时长 {dur//60} 分" if dur else ""
    print(f"已记录：{when.strftime('%Y-%m-%d %H:%M')}　{note}{extra}")
    return 0


def cmd_report(args) -> int:
    from report import generate_daily_report, generate_weekly_report

    store = Store()
    now = dt.datetime.now()
    if args.kind == "daily":
        day = dt.date.fromisoformat(args.date) if args.date else now.date()
        path = generate_daily_report(store, day, now)
    else:
        if args.week:
            iso = now.isocalendar()
            jan4 = dt.date(iso.year, 1, 4)
            start = jan4 - dt.timedelta(days=jan4.weekday()) + dt.timedelta(weeks=args.week - 1)
            path = generate_weekly_report(store, start, now)
        elif args.last:
            start = now.date() - dt.timedelta(days=now.weekday() + 7)
            path = generate_weekly_report(store, start, now)
        else:
            start = now.date() - dt.timedelta(days=now.weekday())
            path = generate_weekly_report(store, start, now)
    print(f"报告已生成：{path}")
    return 0


def cmd_autostart(args) -> int:
    import winreg

    cmdline = f'"{pythonw()}" "{BASE_DIR / "timelog.py"}" start'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS)

    with key:
        if args.action == "install":
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, cmdline)
            print("已安装开机自启。")
            print(f"  注册表：HKCU\\{RUN_KEY}\\{RUN_NAME}")
            print(f"  命令：{cmdline}")
            print("卸载：python timelog.py autostart remove")
            return 0
        if args.action == "remove":
            try:
                winreg.DeleteValue(key, RUN_NAME)
                print("已卸载开机自启。")
            except FileNotFoundError:
                print("本来就没装。")
            return 0
        # status
        try:
            val, _ = winreg.QueryValueEx(key, RUN_NAME)
            print("开机自启：已启用")
            print(f"  {val}")
        except FileNotFoundError:
            print("开机自启：未启用")
            print("  安装：python timelog.py autostart install")
        return 0


def cmd_config(args) -> int:
    cfg = load_config()
    ensure_config_file()
    print(f"配置文件：{CONFIG_PATH}")
    for k, v in cfg.items():
        print(f"  {k} = {v!r}")
    print("\n改配置直接编辑上面这个 json，保存即生效（监控下次启动时读取）。")
    return 0


def cmd_doctor(args) -> int:
    store = Store()
    cfg = load_config()
    print("== 自检 ==")
    print(f"Python {sys.version.split()[0]}　平台 {sys.platform}")
    try:
        import collector

        app, title, cls = collector.foreground()
        idle = collector.idle_seconds()
        print(f"前台窗口：app={app!r} title={title!r} class={cls!r}")
        print(f"空闲判定：{idle:.0f} 秒")
        print("Win32 采样：OK" if app else "Win32 采样：拿到了窗口但进程名为空（可能是权限或系统窗口）")
    except Exception as exc:
        print(f"Win32 采样：失败 —— {exc}")
        return 1
    try:
        probe = store.data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"数据目录可写：{store.data_dir}")
    except Exception as exc:
        print(f"数据目录不可写：{exc}")
        return 1
    print(f"报告目录：{store.report_dir}")
    print(f"空闲阈值：{cfg.get('idle_seconds')} 秒　心跳：{cfg.get('heartbeat_seconds')} 秒")
    print("\n一切正常。启动：python timelog.py start --bg")
    return 0


def cmd_open(args) -> int:
    store = Store()
    store.report_dir.mkdir(parents=True, exist_ok=True)
    os.startfile(str(store.report_dir))  # noqa: S606
    print(f"已打开报告目录：{store.report_dir}")
    return 0


# ---------------------------------------------------------------- 入口


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="timelog",
        description="后台时间记录 + 手动补充 + 每日/每周 Markdown 报告",
    )
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="启动监控")
    s.add_argument("--bg", action="store_true", help="后台静默运行")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("stop", help="停止监控")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("web", help="打开本地网页看板")
    s.add_argument("--port", type=int, help="端口（默认 8765）")
    s.add_argument("--no-browser", action="store_true", help="不自动开浏览器")
    s.set_defaults(func=cmd_web)

    s = sub.add_parser("sync", help="跨机器数据同步（GitHub 私有数据仓）")
    s.add_argument("action", choices=["setup", "run", "status"])
    s.add_argument("url", nargs="?", help="数据仓库地址（setup 时用）")
    s.add_argument("--path", help="本地数据仓库目录")
    s.add_argument("--dry-run", action="store_true", help="只演练，不真的提交推送")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("machine", help="查看 / 修改本机身份")
    s.add_argument("--name", help="给这台机器起个名字，比如 台式机")
    s.set_defaults(func=cmd_machine)

    s = sub.add_parser("status", help="查看状态与今日概况")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("add", help="手动记录一件事")
    s.add_argument("note", help="内容")
    s.add_argument("--dur", help="时长，如 30m / 1h / 1h30m")
    s.add_argument("--at", help="开始时间，如 21:30 或 2026-11-25 21:30（默认现在）")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("report", help="生成报告")
    s.add_argument("kind", choices=["daily", "weekly"])
    s.add_argument("--date", help="日报日期 YYYY-MM-DD（默认今天）")
    s.add_argument("--week", type=int, help="周报第几周（ISO 周数）")
    s.add_argument("--last", action="store_true", help="周报用上一周")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("autostart", help="开机自启")
    s.add_argument("action", choices=["install", "remove", "status"])
    s.set_defaults(func=cmd_autostart)

    s = sub.add_parser("config", help="查看配置")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("doctor", help="自检")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("open", help="打开报告目录")
    s.set_defaults(func=cmd_open)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
