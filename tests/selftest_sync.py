"""多机合并 + 同步往返自测。

全部跑在系统临时目录 + 本地 bare git 仓库里，**不碰用户真实数据，也不联网**。

    python tests/selftest_sync.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

SANDBOX = Path(tempfile.mkdtemp(prefix="timelog-mm-"))
FAILED: list[str] = []
DAY = dt.date(2026, 11, 25)

# 一上来就把环境指向沙箱，绝不能在没设好之前建 Store（否则会读到真实 data/）
os.environ["TIMELOG_MACHINE_FILE"] = str(SANDBOX / "m0" / "machine.json")
os.environ["TIMELOG_DATA_DIR"] = str(SANDBOX / "m0" / "data")
os.environ["TIMELOG_REPORT_DIR"] = str(SANDBOX / "m0" / "reports")
os.environ["TIMELOG_SYNC_FILE"] = str(SANDBOX / "m0" / "sync.json")


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def use_machine(idx: int, name: str, mid: str) -> Path:
    """把环境切到某台"机器"。"""
    d = SANDBOX / f"m{idx}"
    (d / "data").mkdir(parents=True, exist_ok=True)
    (d / "reports").mkdir(parents=True, exist_ok=True)
    os.environ["TIMELOG_MACHINE_FILE"] = str(d / "machine.json")
    os.environ["TIMELOG_DATA_DIR"] = str(d / "data")
    os.environ["TIMELOG_REPORT_DIR"] = str(d / "reports")
    os.environ["TIMELOG_SYNC_FILE"] = str(d / "sync.json")
    (d / "machine.json").write_text(
        json.dumps({"id": mid, "name": name}, ensure_ascii=False), encoding="utf-8"
    )
    return d


def T(h: int, m: int) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(h, m))


def write_events(data_dir: Path, filename: str, mid: str, mname: str, events) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(data_dir) / filename, "a", encoding="utf-8") as f:
        for when, rec in events:
            r = dict(rec)
            r["t"] = when.timestamp()
            r["ts"] = when.replace(microsecond=0).isoformat()
            r["m"] = mid
            r["mn"] = mname
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def merged_summary():
    """按当前环境读合并后的时间线。"""
    from report import segments_for_range, summarize

    from store import Store

    store = Store()
    segs = segments_for_range(store, DAY, DAY)
    return segs, summarize(segs), store


# ---------------------------------------------------------------- 合并数学


def test_no_overlap():
    print("\n[1] 两台机器，时间不重叠")
    d = use_machine(1, "台式机", "aaaaaa")
    write_events(d / "data", "2026-11-25.aaaaaa.jsonl", "aaaaaa", "台式机", [
        (T(9, 0), {"kind": "window", "app": "Code.exe", "title": "x"}),
        (T(12, 0), {"kind": "end"}),
    ])
    write_events(d / "data", "2026-11-25.bbbbbb.jsonl", "bbbbbb", "笔记本", [
        (T(13, 0), {"kind": "window", "app": "Chrome.exe", "title": "y"}),
        (T(16, 0), {"kind": "end"}),
    ])
    _, s, _ = merged_summary()
    check("合并后活跃 6 小时", s["window"] == 21600, f"实际 {s['window']}")
    check("Code.exe 3 小时", s["apps"].get("Code.exe") == 10800, f"实际 {s['apps'].get('Code.exe')}")
    check("Chrome.exe 3 小时", s["apps"].get("Chrome.exe") == 10800, f"实际 {s['apps'].get('Chrome.exe')}")
    check("识别出 2 台设备", len(s["by_machine"]) == 2, f"实际 {list(s['by_machine'])}")
    check("设备分布正确", s["by_machine"].get("台式机") == 10800 and s["by_machine"].get("笔记本") == 10800,
          f"实际 {s['by_machine']}")


def test_overlap():
    print("\n[2] 两台机器，重叠 1 小时")
    d = use_machine(2, "台式机", "aaaaaa")
    write_events(d / "data", "2026-11-25.aaaaaa.jsonl", "aaaaaa", "台式机", [
        (T(9, 0), {"kind": "window", "app": "Code.exe", "title": "x"}),
        (T(12, 0), {"kind": "end"}),
    ])
    write_events(d / "data", "2026-11-25.bbbbbb.jsonl", "bbbbbb", "笔记本", [
        (T(11, 0), {"kind": "window", "app": "Chrome.exe", "title": "y"}),
        (T(14, 0), {"kind": "end"}),
    ])
    _, s, _ = merged_summary()
    check("活跃时长是墙钟并集 5 小时（不是 6 小时）", s["window"] == 18000, f"实际 {s['window']}")
    check("Code.exe 3 小时（先开始的那台优先）", s["apps"].get("Code.exe") == 10800, f"实际 {s['apps'].get('Code.exe')}")
    check("Chrome.exe 2 小时（重叠部分被裁掉）", s["apps"].get("Chrome.exe") == 7200, f"实际 {s['apps'].get('Chrome.exe')}")


def test_end_marker_does_not_truncate():
    print("\n[3] A 机的 end 标记不能截断 B 机正在跑的段")
    d = use_machine(3, "台式机", "aaaaaa")
    write_events(d / "data", "2026-11-25.aaaaaa.jsonl", "aaaaaa", "台式机", [
        (T(9, 0), {"kind": "window", "app": "Code.exe", "title": "x"}),
        (T(10, 0), {"kind": "end"}),
    ])
    write_events(d / "data", "2026-11-25.bbbbbb.jsonl", "bbbbbb", "笔记本", [
        (T(9, 30), {"kind": "window", "app": "Chrome.exe", "title": "y"}),
        (T(11, 0), {"kind": "end"}),
    ])
    segs, s, _ = merged_summary()
    code_segs = [x for x in segs if x["app"] == "Code.exe"]
    chrome_segs = [x for x in segs if x["app"] == "Chrome.exe"]
    check("A 机那段完整到 10:00", any(abs((x["end"] - x["start"]) - 3600) < 1 for x in code_segs),
          f"实际 {[round(x['end']-x['start']) for x in code_segs]}")
    check("B 机那段没被 A 的 end 标记截断", any(abs((x["end"] - x["start"]) - 3600) < 1 for x in chrome_segs),
          f"实际 {[round(x['end']-x['start']) for x in chrome_segs]}")
    check("合计 2 小时", s["window"] == 7200, f"实际 {s['window']}")


def test_manual_not_truncated():
    print("\n[4] 手动记录不参与分组，也不截断自动段")
    d = use_machine(4, "台式机", "aaaaaa")
    write_events(d / "data", "2026-11-25.aaaaaa.jsonl", "aaaaaa", "台式机", [
        (T(9, 0), {"kind": "window", "app": "Code.exe", "title": "x"}),
        (T(10, 0), {"kind": "manual", "note": "中途插一条", "dur": 600}),
        (T(12, 0), {"kind": "end"}),
    ])
    _, s, _ = merged_summary()
    check("自动段没被手动记录截断（3 小时）", s["window"] == 10800, f"实际 {s['window']}")


# ---------------------------------------------------------------- 同步往返


def git(*args: str, cwd: Path) -> tuple[int, str]:
    r = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_sync_roundtrip():
    print("\n[5] 同步往返（本地 bare 仓库模拟 GitHub）")
    remote = SANDBOX / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    code, out = git("init", "--bare", "-b", "main", str(remote), cwd=SANDBOX)
    check("远端仓库建好", code == 0, out.strip()[:200])

    import sync

    # --- 机器 A ---
    dA = use_machine(1, "台式机", "aaaaaa")
    storeA = __import__("store").Store()
    storeA.append({"kind": "window", "app": "Code.exe", "title": "A的活儿"}, T(9, 0))
    storeA.append({"kind": "end"}, T(10, 0))
    ok, msg = sync.setup(str(remote), str(SANDBOX / "repoA"))
    check("A 配置数据仓库", ok, msg[:200])
    ok, msg = sync.run(storeA)
    check("A 首次同步成功（推上去）", ok, msg[-400:])

    # --- 机器 B ---
    dB = use_machine(2, "笔记本", "bbbbbb")
    storeB = __import__("store").Store()
    ok, msg = sync.setup(str(remote), str(SANDBOX / "repoB"))
    check("B 配置数据仓库", ok, msg[:200])
    ok, msg = sync.run(storeB)
    check("B 首次同步成功（拉到 A 的数据）", ok, msg[-400:])

    filesB = sorted(p.name for p in (dB / "data").glob("*.jsonl"))
    check("B 收到了 A 的数据文件", any(".aaaaaa." in f for f in filesB), f"实际 {filesB}")
    recsB = storeB.read_day(DAY)
    check("B 能读到 A 的事件", any(r.get("app") == "Code.exe" for r in recsB), f"共 {len(recsB)} 条")

    # B 写自己的数据再推
    storeB.append({"kind": "window", "app": "Chrome.exe", "title": "B的活儿"}, T(14, 0))
    storeB.append({"kind": "end"}, T(15, 0))
    ok, msg = sync.run(storeB)
    check("B 推送成功", ok, msg[-400:])

    # --- 回到 A ---
    use_machine(1, "台式机", "aaaaaa")
    storeA = __import__("store").Store()
    ok, msg = sync.run(storeA)
    check("A 再同步成功（拉到 B 的数据）", ok, msg[-400:])
    filesA = sorted(p.name for p in (dA / "data").glob("*.jsonl"))
    check("A 收到了 B 的数据文件", any(".bbbbbb." in f for f in filesA), f"实际 {filesA}")

    _, s, _ = merged_summary()
    check("A 合并后能看到两台机器的活跃时间", s["window"] >= 10800 + 3600 - 1, f"实际 {s['window']}")
    check("A 合并后识别出 2 台设备", len(s["by_machine"]) == 2, f"实际 {list(s['by_machine'])}")

    # 报告能正常生成
    from report import generate_daily_report

    path = generate_daily_report(storeA, DAY)
    md = path.read_text(encoding="utf-8")
    check("日报能生成且含设备列", "涉及设备" in md, md[:200])
    check("日报含两台设备名", "台式机" in md and "笔记本" in md)


def main() -> int:
    print("多机合并 + 同步自测")
    print(f"沙箱：{SANDBOX}")
    test_no_overlap()
    test_overlap()
    test_end_marker_does_not_truncate()
    test_manual_not_truncated()
    test_sync_roundtrip()
    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    shutil.rmtree(SANDBOX, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
