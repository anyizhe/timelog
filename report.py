"""日报 / 周报生成（Markdown）。

时间线是怎么算出来的：
  事件按时间排序后，**连续**同 (类型, 应用, 标题) 的事件会合成一段，
  这一段的起点 = 组内第一个事件，终点 = 下一个不同组的事件（或记录结束时刻）。
  监控停止时会补一条 end 标记，于是最后一段有明确终点，不会把「关掉电脑到第二天」算进去。
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

# 采集器每 120 秒写一次心跳（config.json 的 heartbeat_seconds）。
# 用来给"每台机器的最后一个段"封顶，防止旧快照造出几小时的幽灵段。
HEARTBEAT_PAD = 120

# ---------------------------------------------------------------- 时间线构建


def build_segments(records: list[dict], now_ts: float | None = None) -> list[dict]:
    """把事件流压成不重叠的时间段。

    **按机器各自算，再拼起来。** 不能把多台机器的事件混在一条流里分组：
    A 机的 "end" 标记会拦腰截断 B 机同时在跑的那一段（实测踩过）。
    所以先把事件按机器号分开，每台机器内部按老规则成段，最后合并。

    另外，手动记录（kind=manual）是**并行标注**，不参与时间线分组 ——
    否则它会把自动段从中间切断，导致那一段时长被截成 0。
    """
    if now_ts is None:
        now_ts = time.time()
    by_machine: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("kind", "window") == "manual":
            continue
        by_machine.setdefault(rec.get("m", "") or "", []).append(rec)

    segs: list[dict] = []
    for recs in by_machine.values():
        recs.sort(key=lambda r: r.get("t", 0))
        segs.extend(_build_one_machine(recs, now_ts))
    segs.sort(key=lambda s: s["start"])
    return segs


def _build_one_machine(recs: list[dict], now_ts: float) -> list[dict]:
    """单台机器内部：连续同 (类型, 应用, 标题) 的事件合成一段。"""
    groups: list[dict] = []
    for rec in recs:
        kind = rec.get("kind", "window")
        app = rec.get("app", "") or ""
        title = rec.get("title", "") or ""
        key = (kind, app, title)
        t = rec.get("t", 0.0)
        if groups and groups[-1]["key"] == key:
            groups[-1]["n"] += 1
            groups[-1]["last_t"] = t
            continue
        groups.append(
            {
                "key": key,
                "kind": kind,
                "app": app,
                "title": title,
                "machine": rec.get("m", "") or "",
                "mname": rec.get("mn", "") or rec.get("m", "") or "",
                "t": t,
                "last_t": t,
                "n": 1,
            }
        )
    segs: list[dict] = []
    for i, g in enumerate(groups):
        start = g["t"]
        if i + 1 < len(groups):
            end = groups[i + 1]["t"]
        else:
            # 最后一个段**不能无限延伸到"现在"**。
            # 这台机器的数据流可能早就断了（比如另一台电脑拉到的是一份旧快照），
            # 无脑延伸到 now 会凭空造出"用了一整夜"的幽灵段。
            # 最多再宽限一个心跳周期 —— 采集器每 120 秒写一次心跳，
            # 所以"最后一次有动静"之后顶多也就再宽限这么久。
            end = min(now_ts, g["last_t"] + HEARTBEAT_PAD)
        if end < start:
            end = start
        segs.append(
            {
                "kind": g["kind"],
                "app": g["app"],
                "title": g["title"],
                "machine": g["machine"],
                "mname": g["mname"],
                "start": start,
                "end": end,
            }
        )
    return segs


def trim_overlaps(segs: list[dict]) -> list[dict]:
    """跨机器去重：同一时刻只算一次。

    两台机器同时开着（远程连台式机、或笔记本和台式机同时在用）时，
    同一分钟会被两台机器各记一次。这里按开始时间排序，把后一段被前一段
    盖住的时间裁掉，保证"活跃时长"是**墙钟时间的并集**，不会翻倍。

    注意：只让**真实活动段**（window/idle）参与覆盖。end / none 这些占位段
    只是"这台机器到此为止"的边界标记，它们不算活动，也不该吞掉别的机器的时间。
    """
    real = [s for s in segs if s["kind"] in ("window", "idle")]
    marks = [s for s in segs if s["kind"] not in ("window", "idle")]
    ordered = sorted(real, key=lambda s: (s["start"], s["end"]))
    covered: list[tuple[float, float]] = []
    out: list[dict] = []
    for s in ordered:
        pieces = [(s["start"], s["end"])]
        for cs, ce in covered:
            rest = []
            for a, b in pieces:
                if ce <= a or cs >= b:
                    rest.append((a, b))
                    continue
                if cs > a:
                    rest.append((a, min(cs, b)))
                if ce < b:
                    rest.append((max(ce, a), b))
            pieces = rest
            if not pieces:
                break
        covered.append((s["start"], s["end"]))
        for a, b in pieces:
            if b - a >= 1:
                out.append({**s, "start": a, "end": b})
    out.sort(key=lambda s: s["start"])
    return out + marks




def clip_segments(segs: list[dict], start_ts: float, end_ts: float) -> list[dict]:
    """把时间段裁剪到指定区间（跨天的段会被切开）。"""
    out = []
    for s in segs:
        if s["end"] <= start_ts or s["start"] >= end_ts:
            continue
        out.append(
            {
                "kind": s["kind"],
                "app": s["app"],
                "title": s["title"],
                "machine": s.get("machine", ""),
                "mname": s.get("mname", ""),
                "start": max(s["start"], start_ts),
                "end": min(s["end"], end_ts),
            }
        )
    return out


def visible(segs: list[dict]) -> list[dict]:
    """滤掉只为封口用的占位段。"""
    return [s for s in segs if s["kind"] in ("window", "idle") and s["end"] - s["start"] >= 1]


def ignored_apps() -> set[str]:
    """不计入统计的应用（config.json 的 ignore_apps）。

    采集时已经跳过它们了，但**历史数据**里可能还有（配置是后加的），
    所以出报告时再过滤一次 —— 改一下配置，老数据立刻也干净了。
    """
    try:
        from config import load_config

        return {str(a).lower() for a in load_config().get("ignore_apps", []) if a}
    except Exception:
        return set()


def visible_for_stats(segs: list[dict], ignore: set[str] | None = None) -> list[dict]:
    """真正参与统计的段：去掉占位段，再去掉被忽略的应用（悬浮工具之类）。"""
    ign = ignored_apps() if ignore is None else ignore
    return [
        s for s in visible(segs)
        if s["kind"] == "idle" or (s.get("app") or "").lower() not in ign
    ]


def segments_for_range(store, start_date: dt.date, end_date: dt.date, now_ts: float | None = None):
    """取区间内的时间段（多读前后各一天，保证跨天边界正确）。"""
    recs = store.read_range(start_date - dt.timedelta(days=1), end_date + dt.timedelta(days=1))
    segs = build_segments(recs, now_ts)
    s_ts = dt.datetime.combine(start_date, dt.time.min).timestamp()
    # 结束边界取「次日零点」而不是 23:59:59.999999，否则最后一段会少算零点前那一秒
    e_ts = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min).timestamp()
    return trim_overlaps(clip_segments(segs, s_ts, e_ts))


# ---------------------------------------------------------------- 统计


def summarize(segs: list[dict]) -> dict:
    apps: dict[str, float] = {}
    titles: dict[tuple[str, str], float] = {}
    title_machines: dict[tuple[str, str], list[str]] = {}
    by_machine: dict[str, float] = {}
    by_app_machine: dict[tuple[str, str], float] = {}
    idle = 0.0
    window = 0.0
    switches = 0
    prev_window_app = None      # 上一个「窗口段」的应用，用来数切换次数（空闲不打断）
    run_app = ""                # 当前连续投入的应用（空闲会打断）
    run_start = 0.0
    longest: tuple[str, str, float] = ("", "", 0.0)
    tail_end = 0.0

    def flush_run(upto: float) -> None:
        nonlocal longest
        if run_app and (upto - run_start) > longest[2]:
            longest = (run_app, "", upto - run_start)

    for s in visible_for_stats(segs):
        dur = s["end"] - s["start"]
        tail_end = max(tail_end, s["end"])
        if s["kind"] == "idle":
            idle += dur
            flush_run(s["start"])   # 空闲打断连续投入
            run_app = ""
            continue
        window += dur
        apps[s["app"]] = apps.get(s["app"], 0.0) + dur
        titles[(s["app"], s["title"])] = titles.get((s["app"], s["title"]), 0.0) + dur
        mname = s.get("mname") or s.get("machine") or "未标记"
        tm = title_machines.setdefault((s["app"], s["title"]), [])
        if mname not in tm:
            tm.append(mname)
        by_machine[mname] = by_machine.get(mname, 0.0) + dur
        k = (s["app"], mname)
        by_app_machine[k] = by_app_machine.get(k, 0.0) + dur
        if s["app"] != prev_window_app:
            switches += 1
        if s["app"] != run_app:
            flush_run(s["start"])
            run_app = s["app"]
            run_start = s["start"]
        prev_window_app = s["app"]
    flush_run(tail_end)
    return {
        "apps": apps,
        "titles": titles,
        "title_machines": title_machines,
        "idle": idle,
        "window": window,
        "switches": switches,
        "longest": longest,
        "by_machine": by_machine,
        "by_app_machine": by_app_machine,
    }


def manual_entries(store, start_date: dt.date, end_date: dt.date) -> list[dict]:
    recs = store.read_range(start_date, end_date)
    return [r for r in recs if r.get("kind") == "manual"]


def hour_histogram(segs: list[dict]) -> list[float]:
    buckets = [0.0] * 24
    for s in visible_for_stats(segs):
        if s["kind"] != "window":
            continue
        t0 = dt.datetime.fromtimestamp(s["start"])
        t1 = dt.datetime.fromtimestamp(s["end"])
        # 逐小时切分，跨小时的活动分摊到各小时
        cur = t0
        while cur < t1:
            hour_end = cur.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
            seg_end = min(hour_end, t1)
            buckets[cur.hour] += (seg_end - cur).total_seconds()
            cur = seg_end
    return buckets


# ---------------------------------------------------------------- 格式化


def fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} 秒"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} 分"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} 小时" + (f" {m:02d} 分" if m else "")
    d, h = divmod(h, 24)
    return f"{d} 天 {h} 小时"


def fmt_short(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if m else f"{h}h"


def fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def bar(value: float, vmax: float, width: int = 20) -> str:
    if vmax <= 0:
        return ""
    n = int(round(width * value / vmax))
    return "█" * n + "░" * (width - n)


def _top_titles(titles: dict, app: str, n: int = 3) -> str:
    items = sorted(
        ((t, d) for (a, t), d in titles.items() if a == app and t), key=lambda x: -x[1]
    )
    if not items:
        return "—"
    return "、".join(f"{t[:28]}" for t, _ in items[:n])


def _machine_note(by_app_machine: dict, app: str) -> str:
    """某个应用在各台设备上分别用了多久。"""
    items = sorted(
        ((m, d) for (a, m), d in by_app_machine.items() if a == app), key=lambda x: -x[1]
    )
    if not items:
        return "—"
    return "、".join(f"{m} {fmt_dur(d)}" for m, d in items[:3])


def _pct(x: float, total: float) -> str:
    if total <= 0:
        return "0%"
    return f"{x / total * 100:.0f}%"


def _titles_table(titles: dict, title_machines: dict | None = None, limit: int = 20) -> list[str]:
    """「我今天到底碰了哪些东西」——按停留时长排的窗口标题清单，带设备来源。"""
    items = sorted(titles.items(), key=lambda kv: -kv[1])
    lines = ["| 窗口标题 | 应用 | 设备 | 时长 |", "|---|---|---|---|"]
    n = 0
    for (app, title), sec in items:
        if not title:
            continue
        devs = "、".join((title_machines or {}).get((app, title), [])) or "—"
        lines.append(f"| {title} | {app} | {devs} | {fmt_dur(sec)} |")
        n += 1
        if n >= limit:
            break
    return lines


# ---------------------------------------------------------------- 观察


def daily_insights(store, day: dt.date, segs: list[dict], summ: dict, manual: list[dict]) -> list[str]:
    out: list[str] = []
    total = summ["window"]
    if total < 60:
        out.append("今天几乎没有采集到窗口活动，可能是监控没启动，或者一直在无标题窗口里操作。")
        return out

    # 与最近 7 天均值比
    prev_days = [day - dt.timedelta(days=i) for i in range(1, 8)]
    prev_totals = []
    for d in prev_days:
        v = summarize(segments_for_range(store, d, d))
        if v["window"] > 60:
            prev_totals.append(v["window"])
    if prev_totals:
        avg = sum(prev_totals) / len(prev_totals)
        diff = (total - avg) / avg * 100 if avg else 0
        if abs(diff) >= 20:
            out.append(f"活跃时长比最近 {len(prev_totals)} 天均值{'多' if diff > 0 else '少'}了 {abs(diff):.0f}%。")
        else:
            out.append(f"活跃时长与最近 {len(prev_totals)} 天均值基本持平。")

    # 集中度
    if summ["apps"]:
        top_app, top_sec = max(summ["apps"].items(), key=lambda kv: kv[1])
        if top_sec / total >= 0.5:
            out.append(f"一半以上时间都在 {top_app}（{_pct(top_sec, total)}），注意力比较集中。")
        if len(summ["apps"]) >= 8:
            out.append(f"一天切到了 {len(summ['apps'])} 个应用、{summ['switches']} 次切换，注意力偏碎。")

    # 最长连续段
    if summ["longest"][0]:
        app, _, sec = summ["longest"]
        if sec >= 1800:
            out.append(f"最长的一段连续投入是 {app}，约 {fmt_dur(sec)}。")

    # 高峰时段
    hist = hour_histogram(segs)
    if max(hist) > 0:
        peak = hist.index(max(hist))
        out.append(f"最活跃的时段是 {peak:02d}:00 前后。")

    if summ["idle"] > 1800:
        out.append(f"有 {fmt_dur(summ['idle'])} 处于空闲（键鼠长时间没动），其中可能有离开电脑的时间。")

    if manual:
        out.append(f"你手动记了 {len(manual)} 条事情，自动记录未必覆盖这些内容，建议对着时间线补一遍。")
    return out


def weekly_insights(days: list[tuple[dt.date, dict]], summ: dict, manual: list[dict]) -> list[str]:
    out: list[str] = []
    active = [(d, s) for d, s in days if s["window"] > 60]
    if not active:
        return ["这一周没有采集到有效活动数据。"]
    total = sum(s["window"] for _, s in active)
    avg = total / len(active)
    best_d, best_s = max(active, key=lambda x: x[1]["window"])
    worst_d, worst_s = min(active, key=lambda x: x[1]["window"])
    out.append(f"有记录的天里，{best_d.strftime('%m-%d')} 最投入（{fmt_dur(best_s['window'])}），{worst_d.strftime('%m-%d')} 最少（{fmt_dur(worst_s['window'])}）。")
    out.append(f"日均活跃 {fmt_dur(avg)}，全天都在电脑前的日子有 {sum(1 for _, s in active if s['window'] >= 6*3600)} 天。")

    if summ["apps"]:
        top_app, top_sec = max(summ["apps"].items(), key=lambda kv: kv[1])
        share = top_sec / total * 100 if total else 0
        out.append(f"本周投入最多的应用是 {top_app}，占全部活跃时间的 {share:.0f}%。")
    if len(summ["apps"]) >= 12:
        out.append(f"本周一共用到 {len(summ['apps'])} 个应用，工具面比较分散。")
    if manual:
        out.append(f"本周手动记录 {len(manual)} 条。")
    return out


# ---------------------------------------------------------------- 渲染


def render_daily(store, day: dt.date, segs: list[dict], manual: list[dict], now: dt.datetime) -> str:
    summ = summarize(segs)
    total = summ["window"]
    man_dur = sum(float(m.get("dur", 0) or 0) for m in manual)
    is_today = day == now.date()
    L: list[str] = []
    L.append(f"# {day.strftime('%Y-%m-%d')} 时间报告")
    L.append("")
    L.append(f"> 生成于 {now.strftime('%Y-%m-%d %H:%M')}" + ("（今日数据仍在累积中）" if is_today else "") + "　数据源：前台窗口活动 + 手动记录")
    L.append("")
    L.append("## 一、总览")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 屏幕活跃时长 | {fmt_dur(total)} |")
    L.append(f"| 空闲时长 | {fmt_dur(summ['idle'])} |")
    L.append(f"| 手动记录 | {len(manual)} 条 / 共 {fmt_dur(man_dur)} |")
    L.append(f"| 涉及应用 | {len(summ['apps'])} 个 |")
    L.append(f"| 应用切换次数 | {summ['switches']} 次 |")
    L.append(f"| 涉及设备 | {len(summ['by_machine'])} 台（{'、'.join(sorted(summ['by_machine'])) or '—'}） |")
    L.append("")

    L.append("## 二、应用使用排行")
    L.append("")
    if summ["apps"]:
        L.append("| # | 应用 | 时长 | 占比 | 设备分布 | 主要窗口 |")
        L.append("|---|---|---|---|---|---|")
        ranked = sorted(summ["apps"].items(), key=lambda kv: -kv[1])
        for i, (app, sec) in enumerate(ranked, 1):
            L.append(
                f"| {i} | {app} | {fmt_dur(sec)} | {_pct(sec, total)} | "
                f"{_machine_note(summ['by_app_machine'], app)} | {_top_titles(summ['titles'], app)} |"
            )
    else:
        L.append("_当天没有采集到窗口活动。_")
    L.append("")

    L.append("## 三、今天碰到的东西")
    L.append("")
    if summ["titles"]:
        L.extend(_titles_table(summ["titles"], summ.get("title_machines")))
        L.append("")
        L.append("_（按停留时长排序；同一件事中途切走再回来会合并成一条）_")
    else:
        L.append("_没有采集到窗口标题。_")
    L.append("")

    L.append("## 四、时间线")
    L.append("")
    vis = visible_for_stats(segs)
    if vis:
        L.append("| 时段 | 应用 | 窗口标题 | 时长 | 设备 |")
        L.append("|---|---|---|---|---|")
        for s in vis:
            app = "（空闲）" if s["kind"] == "idle" else s["app"]
            title = "—" if s["kind"] == "idle" else (s["title"] or "—")
            L.append(
                f"| {fmt_time(s['start'])}–{fmt_time(s['end'])} | {app} | {title} | "
                f"{fmt_dur(s['end']-s['start'])} | {s.get('mname') or '未标记'} |"
            )
    else:
        L.append("_当天没有时间线数据。_")
    L.append("")

    L.append("## 五、手动记录")
    L.append("")
    if manual:
        for m in manual:
            t = dt.datetime.fromtimestamp(m.get("t", 0))
            dur = m.get("dur")
            span = f"{t.strftime('%H:%M')}–{(t + dt.timedelta(seconds=int(dur))).strftime('%H:%M')}" if dur else f"{t.strftime('%H:%M')}"
            L.append(f"- {span}　{m.get('note', '')}" + (f"（{fmt_dur(dur)}）" if dur else ""))
        L.append("")
        L.append("_手动记录的时间可能与上面的自动时间线重叠，这里的合计时长仅供参考。_")
    else:
        L.append("_当天没有手动记录。_")
    L.append("")

    L.append("## 六、观察")
    L.append("")
    for line in daily_insights(store, day, segs, summ, manual):
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


def render_weekly(store, start: dt.date, end: dt.date, manual: list[dict], now: dt.datetime) -> str:
    L: list[str] = []
    iso_week = start.isocalendar()
    L.append(f"# {iso_week.year} 年第 {iso_week.week} 周 周报（{start.strftime('%m-%d')} ~ {end.strftime('%m-%d')}）")
    L.append("")
    L.append(f"> 生成于 {now.strftime('%Y-%m-%d %H:%M')}　数据源：前台窗口活动 + 手动记录")
    L.append("")

    days: list[tuple[dt.date, dict]] = []
    all_segs: list[dict] = []
    d = start
    while d <= end:
        segs = segments_for_range(store, d, d)
        all_segs.extend(segs)
        days.append((d, summarize(segs)))
        d += dt.timedelta(days=1)
    week_summ = summarize(all_segs)
    man_dur = sum(float(m.get("dur", 0) or 0) for m in manual)
    active_days = [x for x in days if x[1]["window"] > 60]

    L.append("## 一、总览")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 本周活跃时长 | {fmt_dur(week_summ['window'])} |")
    L.append(f"| 日均活跃 | {fmt_dur(week_summ['window'] / max(1, len(active_days)))} |")
    L.append(f"| 有记录的天数 | {len(active_days)} / {len(days)} |")
    L.append(f"| 空闲时长 | {fmt_dur(week_summ['idle'])} |")
    L.append(f"| 手动记录 | {len(manual)} 条 / 共 {fmt_dur(man_dur)} |")
    L.append(f"| 涉及应用 | {len(week_summ['apps'])} 个 |")
    L.append(f"| 涉及设备 | {len(week_summ['by_machine'])} 台（{'、'.join(sorted(week_summ['by_machine'])) or '—'}） |")
    L.append("")

    L.append("## 二、每日分布")
    L.append("")
    vmax = max([s["window"] for _, s in days] + [1])
    L.append("```")
    for day, s in days:
        mark = "  ←" if s["window"] == vmax and vmax > 0 else ""
        L.append(f"{day.strftime('%m-%d')}  {bar(s['window'], vmax)}  {fmt_short(s['window']):>7}{mark}")
    L.append("```")
    L.append("")
    L.append("| 日期 | 活跃 | 空闲 | 手动 | 应用数 | 设备 |")
    L.append("|---|---|---|---|---|---|")
    for day, s in days:
        day_manual = [m for m in manual if dt.datetime.fromtimestamp(m.get("t", 0)).date() == day]
        dm = sum(float(m.get("dur", 0) or 0) for m in day_manual)
        L.append(
            f"| {day.strftime('%m-%d')} | {fmt_dur(s['window'])} | {fmt_dur(s['idle'])} | {fmt_dur(dm)} | "
            f"{len(s['apps'])} | {'、'.join(sorted(s['by_machine'])) or '—'} |"
        )
    L.append("")

    L.append("## 三、本周应用排行")
    L.append("")
    if week_summ["apps"]:
        L.append("| # | 应用 | 本周时长 | 占比 | 设备分布 | 主要窗口 |")
        L.append("|---|---|---|---|---|---|")
        ranked = sorted(week_summ["apps"].items(), key=lambda kv: -kv[1])
        for i, (app, sec) in enumerate(ranked, 1):
            L.append(
                f"| {i} | {app} | {fmt_dur(sec)} | {_pct(sec, week_summ['window'])} | "
                f"{_machine_note(week_summ['by_app_machine'], app)} | {_top_titles(week_summ['titles'], app)} |"
            )
    else:
        L.append("_本周没有采集到窗口活动。_")
    L.append("")

    L.append("## 四、本周碰到的东西")
    L.append("")
    if week_summ["titles"]:
        L.extend(_titles_table(week_summ["titles"], week_summ.get("title_machines"), limit=25))
        L.append("")
        L.append("_（按停留时长排序；同一件事中途切走再回来会合并成一条）_")
    else:
        L.append("_本周没有采集到窗口标题。_")
    L.append("")

    L.append("## 五、时段活跃度（0–23 时）")
    L.append("")
    hist = hour_histogram(all_segs)
    hmax = max(hist + [1])
    L.append("```")
    for h in range(24):
        L.append(f"{h:02d}:00  {bar(hist[h], hmax, 24)}  {fmt_short(hist[h]):>7}")
    L.append("```")
    L.append("")

    L.append("## 六、手动记录")
    L.append("")
    if manual:
        for m in manual:
            t = dt.datetime.fromtimestamp(m.get("t", 0))
            dur = m.get("dur")
            span = f"{t.strftime('%m-%d %H:%M')}–{(t + dt.timedelta(seconds=int(dur))).strftime('%H:%M')}" if dur else f"{t.strftime('%m-%d %H:%M')}"
            L.append(f"- {span}　{m.get('note', '')}" + (f"（{fmt_dur(dur)}）" if dur else ""))
        L.append("")
        L.append("_手动记录的时间可能与上面的自动时间线重叠，这里的合计时长仅供参考。_")
    else:
        L.append("_本周没有手动记录。_")
    L.append("")

    L.append("## 七、观察")
    L.append("")
    for line in weekly_insights(days, week_summ, manual):
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 落盘


def generate_daily_report(store, day: dt.date, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now()
    segs = segments_for_range(store, day, day)
    manual = manual_entries(store, day, day)
    md = render_daily(store, day, segs, manual, now)
    path = store.report_dir / f"daily-{day.isoformat()}.md"
    path.write_text(md, encoding="utf-8")
    return path


def generate_weekly_report(store, week_start: dt.date, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now()
    week_start = week_start - dt.timedelta(days=week_start.weekday())
    week_end = week_start + dt.timedelta(days=6)
    manual = manual_entries(store, week_start, week_end)
    md = render_weekly(store, week_start, week_end, manual, now)
    path = store.report_dir / f"weekly-{week_start.isocalendar()[1]:02d}-{week_start.isocalendar()[0]}.md"
    path.write_text(md, encoding="utf-8")
    return path
