"""数据存储：按天 + 按机器分文件的 JSONL 事件流。

每行一条事件：
    {"t": 1753450000.5, "ts": "2026-07-25T20:06:40", "kind": "window",
     "app": "Code.exe", "title": "a.py", "m": "a3f9c2", "mn": "台式机"}

文件名带机器号（`2026-07-25.a3f9c2.jsonl`），所以两台电脑同步到同一个仓库时
**永远不会撞文件名**，git pull/push 不需要任何冲突处理。

数据目录可用环境变量 TIMELOG_DATA_DIR 覆盖（自测沙箱用）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from pathlib import Path

from machine import load_machine

BASE_DIR = Path(__file__).resolve().parent

DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def default_data_dir() -> Path:
    env = os.environ.get("TIMELOG_DATA_DIR")
    if env:
        return Path(env)
    return BASE_DIR / "data"


def default_report_dir() -> Path:
    env = os.environ.get("TIMELOG_REPORT_DIR")
    if env:
        return Path(env)
    return BASE_DIR / "reports"


class Store:
    def __init__(self, data_dir: str | Path | None = None, report_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.report_dir = Path(report_dir) if report_dir else default_report_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.machine = load_machine()
        self._lock = threading.Lock()

    # ---------- 写 ----------

    def own_day_file(self, day: dt.date) -> Path:
        """本机当天的数据文件（文件名带机器号）。"""
        return self.data_dir / f"{day.isoformat()}.{self.machine['id']}.jsonl"

    def day_file(self, day: dt.date) -> Path:
        return self.own_day_file(day)

    def day_files(self, day: dt.date) -> list[Path]:
        """当天所有机器的数据文件（含历史的无机器后缀旧文件）。"""
        return sorted(self.data_dir.glob(f"{day.isoformat()}*.jsonl"))

    def append(self, rec: dict, when: dt.datetime | None = None) -> dict:
        when = when or dt.datetime.now()
        rec = dict(rec)
        rec.setdefault("t", when.timestamp())
        rec.setdefault("ts", when.replace(microsecond=0).isoformat())
        rec.setdefault("m", self.machine["id"])
        rec.setdefault("mn", self.machine["name"])
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            with open(self.own_day_file(when.date()), "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        return rec

    def append_manual(self, note: str, start: dt.datetime, dur: int | None = None) -> dict:
        rec = {"kind": "manual", "note": note}
        if dur:
            rec["dur"] = int(dur)
        return self.append(rec, start)

    # ---------- 读 ----------

    def read_day(self, day: dt.date) -> list[dict]:
        """读当天**所有机器**的事件，按时间排序。"""
        out: list[dict] = []
        for path in self.day_files(day):
            out.extend(self._read_file(path))
        out.sort(key=lambda r: r.get("t", 0))
        return out

    @staticmethod
    def _read_file(path: Path) -> list[dict]:
        out: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # 半行/坏行，跳过不崩
        except OSError:
            return out
        return out

    def read_range(self, start: dt.date, end: dt.date) -> list[dict]:
        out: list[dict] = []
        day = start
        while day <= end:
            out.extend(self.read_day(day))
            day += dt.timedelta(days=1)
        out.sort(key=lambda r: r.get("t", 0))
        return out

    def available_days(self) -> list[dt.date]:
        days = set()
        for p in self.data_dir.glob("*.jsonl"):
            m = DATE_PREFIX.match(p.stem)
            if m:
                try:
                    days.add(dt.date.fromisoformat(m.group(1)))
                except ValueError:
                    continue
        return sorted(days)

    def machines_in_day(self, day: dt.date) -> list[str]:
        """当天有数据的机器名列表（去重、保序）。"""
        names: list[str] = []
        for rec in self.read_day(day):
            name = rec.get("mn") or rec.get("m") or ""
            if name and name not in names:
                names.append(name)
        return names

    # ---------- 运行时文件 ----------

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "collector.pid"

    @property
    def stop_flag(self) -> Path:
        return self.data_dir / "stop.flag"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "collector.log"

    def clear_stop_flag(self) -> None:
        if self.stop_flag.exists():
            try:
                self.stop_flag.unlink()
            except OSError:
                pass
