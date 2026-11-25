"""自测：用合成数据验证时间线数学 + 报告渲染。

必须跑在临时沙箱目录里，绝不能碰用户的真实 data/。
    python tests/selftest.py
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

SANDBOX = Path(tempfile.mkdtemp(prefix="timelog-selftest-"))
os.environ["TIMELOG_DATA_DIR"] = str(SANDBOX / "data")
os.environ["TIMELOG_REPORT_DIR"] = str(SANDBOX / "reports")
os.environ["TIMELOG_MACHINE_FILE"] = str(SANDBOX / "machine.json")

from collector import Collector  # noqa: E402
from report import build_segments, clip_segments, generate_daily_report, generate_weekly_report, summarize  # noqa: E402
from store import Store  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def T(day: int, h: int, m: int) -> dt.datetime:
    return dt.datetime(2026, 11, day, h, m)


def main() -> int:
    store = Store()

    # ---- 构造 2026-11-23（周一）的活动 ----
    ev = [
        (T(23, 9, 0), {"kind": "window", "app": "Code.exe", "title": "a.py"}),
        (T(23, 9, 30), {"kind": "window", "app": "Code.exe", "title": "a.py"}),  # 心跳
        (T(23, 10, 0), {"kind": "window", "app": "Chrome.exe", "title": "搜索"}),
        (T(23, 10, 30), {"kind": "idle"}),
        (T(23, 11, 0), {"kind": "window", "app": "Code.exe", "title": "b.py"}),
        (T(23, 12, 0), {"kind": "end"}),                       # 监控停止
        (T(23, 14, 0), {"kind": "none", "title": "__session_start__"}),  # 重新启动
        (T(23, 14, 0), {"kind": "window", "app": "Code.exe", "title": "b.py"}),
        (T(23, 15, 0), {"kind": "window", "app": "Chrome.exe", "title": "docs"}),
        (T(23, 15, 30), {"kind": "end"}),
        # 跨午夜的一段（23:30 → 次日 01:00）
        (T(24, 23, 30), {"kind": "window", "app": "Code.exe", "title": "late.py"}),
        (T(25, 0, 15), {"kind": "window", "app": "Code.exe", "title": "late.py"}),
        (T(25, 1, 0), {"kind": "window", "app": "Code.exe", "title": "morning.py"}),
        (T(25, 1, 30), {"kind": "end"}),
    ]
    for when, rec in ev:
        store.append(dict(rec, t=when.timestamp()), when)

    # 手动记录
    store.append_manual("开需求评审会", T(23, 10, 0), 45 * 60)
    store.append_manual("想了下方案", T(23, 16, 0))

    print("\n[1] 时间线数学")
    from report import segments_for_range

    d23 = segments_for_range(store, dt.date(2026, 11, 23), dt.date(2026, 11, 23))
    s23 = summarize(d23)
    check("11-23 活跃 4 小时", s23["window"] == 14400, f"实际 {s23['window']}")
    check("11-23 空闲 30 分", s23["idle"] == 1800, f"实际 {s23['idle']}")
    check("11-23 Code.exe 3 小时", s23["apps"].get("Code.exe") == 10800, f"实际 {s23['apps'].get('Code.exe')}")
    check("11-23 Chrome.exe 1 小时", s23["apps"].get("Chrome.exe") == 3600, f"实际 {s23['apps'].get('Chrome.exe')}")
    longest = max((x["end"] - x["start"]) for x in d23 if x["kind"] == "window")
    check("11-23 无超长段（12:00-14:00 空档没被算进去）", longest <= 3601, f"最长 {longest}")
    check("11-23 分段数 5", len([x for x in d23 if x["kind"] == "window"]) == 5, f"实际 {len([x for x in d23 if x['kind']=='window'])}")

    d24 = segments_for_range(store, dt.date(2026, 11, 24), dt.date(2026, 11, 24))
    s24 = summarize(d24)
    check("11-24 只算到午夜（23:30-24:00 = 30 分）", s24["window"] == 1800, f"实际 {s24['window']}")

    d25 = segments_for_range(store, dt.date(2026, 11, 25), dt.date(2026, 11, 25))
    s25 = summarize(d25)
    check("11-25 跨夜段后半截 00:00-01:00 = 60 分", s25["window"] == 5400, f"实际 {s25['window']}")

    print("\n[1b] 手动记录不能切断自动时间线")
    # 手动记录插在 Chrome 段中间（10:00），Chrome 段仍应完整到 10:30
    chrome = [x for x in d23 if x["app"] == "Chrome.exe"]
    check("手动记录未截断 Chrome 段", any(abs((x["end"] - x["start"]) - 1800) < 1 for x in chrome),
          f"Chrome 段: {[round(x['end']-x['start']) for x in chrome]}")
    check("手动记录本身不算活跃时长", s23["window"] == 14400, f"实际 {s23['window']}")

    print("\n[2] 段落无缝且不重叠")
    allsegs = build_segments(store.read_range(dt.date(2026, 11, 22), dt.date(2026, 11, 26)), T(26, 0, 0).timestamp())
    shown = [x for x in allsegs if x["kind"] in ("window", "idle")]
    ok = True
    for a, b in zip(shown, shown[1:]):
        if b["start"] < a["end"] - 0.001:
            ok = False
    check("相邻段不重叠", ok)
    check("活动段总时长 = 各段之和", abs(sum(x["end"] - x["start"] for x in shown) - 23400) < 1,
          f"实际 {sum(x['end'] - x['start'] for x in shown)}")

    print("\n[3] 报告渲染")
    p = generate_daily_report(store, dt.date(2026, 11, 23))
    md = p.read_text(encoding="utf-8")
    check("日报文件存在", p.exists())
    check("日报含标题", "# 2026-11-23 时间报告" in md)
    check("日报含应用排行", "Code.exe" in md and "Chrome.exe" in md)
    check("日报含时间线表头", "| 时段 | 应用 | 窗口标题 | 时长 |" in md)
    check("日报含手动记录", "开需求评审会" in md)
    check("日报含观察", "## 六、观察" in md)
    check("日报含「碰到的东西」清单", "## 三、今天碰到的东西" in md and "a.py" in md)
    check("日报没把空档算进去", "12:00–14:00" not in md)

    pw = generate_weekly_report(store, dt.date(2026, 11, 23))
    wmd = pw.read_text(encoding="utf-8")
    check("周报文件存在", pw.exists())
    check("周报含 ISO 周号", "第 48 周" in wmd, wmd.splitlines()[0] if wmd else "")
    check("周报含每日分布", "## 二、每日分布" in wmd)
    check("周报含时段活跃度", "## 五、时段活跃度" in wmd)
    check("周报含应用排行", "## 三、本周应用排行" in wmd)
    check("周报含「碰到的东西」清单", "## 四、本周碰到的东西" in wmd)
    check("周报含观察", "## 七、观察" in wmd)

    print("\n[4] 隐私遮蔽")
    cfg = {"hide_title_keywords": ["密码", "password"], "hide_apps": ["keepsecret.exe"], "ignore_apps": ["nosecret.exe"]}
    c = Collector(store, cfg)
    check("标题含「密码」被遮", c._apply_privacy("Chrome.exe", "我的密码管理器")[1] == "[已隐藏]")
    check("标题含 password 被遮", c._apply_privacy("Chrome.exe", "Login - Password")[1] == "[已隐藏]")
    check("hide_apps 只藏标题", c._apply_privacy("keepsecret.exe", "任意标题") == ("keepsecret.exe", "[已隐藏]"))
    check("ignore_apps 完全不记", c._apply_privacy("nosecret.exe", "任意标题") == ("", ""))

    print("\n[5] 时长/时间解析")
    from timelog import parse_duration, parse_when

    check("30m → 1800", parse_duration("30m") == 1800)
    check("1h → 3600", parse_duration("1h") == 3600)
    check("1h30m → 5400", parse_duration("1h30m") == 5400)
    check("90 → 5400（默认分钟）", parse_duration("90") == 5400)
    check("21:30 解析", parse_when("21:30").strftime("%H:%M") == "21:30")
    check("2026-01-02 03:04 解析", parse_when("2026-01-02 03:04").strftime("%Y-%m-%d %H:%M") == "2026-01-02 03:04")

    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    print(f"沙箱目录：{SANDBOX}")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
