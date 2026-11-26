#!/usr/bin/env python3
"""timelog 本地网页看板。

只监听 127.0.0.1，不对外暴露。接口：
    GET  /                    前端页面
    GET  /api/status          采集器状态、目录、已有数据天数
    GET  /api/day?date=...   某天的完整数据（分段/应用/标题/手动记录/观察）
    GET  /api/week?week=N     某周的数据（ISO 周号，缺省本周）
    GET  /api/report?kind=    生成的报告 Markdown 原文
    POST /api/add             手动记一条
    POST /api/collector/start 启动 / 停止采集器
    GET  /api/config          读取配置
    POST /api/config          保存配置
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import report as R  # noqa: E402
from config import CONFIG_PATH, load_config, save_config  # noqa: E402
from store import Store  # noqa: E402
from timelog import do_start, do_stop, parse_duration, parse_when  # noqa: E402

UI_DIR = BASE_DIR / "ui"
DEFAULT_PORT = 8765
APP_TAG = "timelog"


# ---------------------------------------------------------------- 数据打包


def _seg_payload(segs) -> list[dict]:
    return [
        {
            "start": s["start"],
            "end": s["end"],
            "app": s["app"],
            "title": s["title"],
            "kind": s["kind"],
            "machine": s.get("machine", ""),
            "mname": s.get("mname", ""),
            "seconds": s["end"] - s["start"],
        }
        for s in R.visible(segs)
    ]


def _apps_payload(summ: dict) -> list[dict]:
    total = summ["window"] or 1
    ranked = sorted(summ["apps"].items(), key=lambda kv: -kv[1])
    out = []
    for app, sec in ranked:
        tops = [
            {"title": t, "seconds": d}
            for (a, t), d in sorted(summ["titles"].items(), key=lambda kv: -kv[1])
            if a == app and t
        ][:5]
        machines = [
            {"name": m, "seconds": d}
            for (a, m), d in sorted(summ["by_app_machine"].items(), key=lambda kv: -kv[1])
            if a == app
        ]
        out.append({"app": app, "seconds": sec, "share": sec / total, "titles": tops, "machines": machines})
    return out


def _titles_payload(summ: dict) -> list[dict]:
    tm = summ.get("title_machines", {})
    return [
        {
            "title": t,
            "app": a,
            "seconds": d,
            "machines": tm.get((a, t), []),
        }
        for (a, t), d in sorted(summ["titles"].items(), key=lambda kv: -kv[1])
        if t
    ]


def _manual_payload(manual: list[dict]) -> list[dict]:
    return [
        {"t": m.get("t", 0), "note": m.get("note", ""), "dur": m.get("dur")} for m in manual
    ]


def day_payload(store: Store, day: dt.date) -> dict:
    segs = R.segments_for_range(store, day, day)
    summ = R.summarize(segs)
    manual = R.manual_entries(store, day, day)
    return {
        "date": day.isoformat(),
        "is_today": day == dt.date.today(),
        "window": summ["window"],
        "idle": summ["idle"],
        "switches": summ["switches"],
        "app_count": len(summ["apps"]),
        "machines": summ["by_machine"],
        "apps": _apps_payload(summ),
        "titles": _titles_payload(summ),
        "segments": _seg_payload(segs),
        "manual": _manual_payload(manual),
        "manual_seconds": sum(float(m.get("dur", 0) or 0) for m in manual),
        "insights": R.daily_insights(store, day, segs, summ, manual),
    }


def week_payload(store: Store, week_start: dt.date) -> dict:
    week_start = week_start - dt.timedelta(days=week_start.weekday())
    week_end = week_start + dt.timedelta(days=6)
    days = []
    all_segs = []
    d = week_start
    while d <= week_end:
        segs = R.segments_for_range(store, d, d)
        all_segs.extend(segs)
        s = R.summarize(segs)
        dm = [
            m
            for m in R.manual_entries(store, d, d)
        ]
        days.append(
            {
                "date": d.isoformat(),
                "weekday": d.weekday(),
                "is_today": d == dt.date.today(),
                "is_future": d > dt.date.today(),
                "window": s["window"],
                "idle": s["idle"],
                "app_count": len(s["apps"]),
                "machines": s["by_machine"],
                "manual": len(dm),
                "manual_seconds": sum(float(m.get("dur", 0) or 0) for m in dm),
            }
        )
        d += dt.timedelta(days=1)
    manual = R.manual_entries(store, week_start, week_end)
    summ = R.summarize(all_segs)
    iso = week_start.isocalendar()
    return {
        "iso_year": iso.year,
        "iso_week": iso.week,
        "start": week_start.isoformat(),
        "end": week_end.isoformat(),
        "days": days,
        "window": summ["window"],
        "idle": summ["idle"],
        "app_count": len(summ["apps"]),
        "machines": summ["by_machine"],
        "apps": _apps_payload(summ),
        "titles": _titles_payload(summ),
        "hours": R.hour_histogram(all_segs),
        "manual": _manual_payload(manual),
        "manual_seconds": sum(float(m.get("dur", 0) or 0) for m in manual),
        "insights": R.weekly_insights([(dt.date.fromisoformat(x["date"]), R.summarize(R.segments_for_range(store, dt.date.fromisoformat(x["date"]), dt.date.fromisoformat(x["date"])))) for x in days], summ, manual),
    }


def status_payload(store: Store) -> dict:
    from timelog import read_pid

    pid = read_pid(store)
    running = False
    if pid:
        from timelog import pid_alive

        running = pid_alive(pid)
    today = dt.date.today()
    segs = R.segments_for_range(store, today, today)
    s = R.summarize(segs)
    days = store.available_days()
    return {
        "app": APP_TAG,
        "running": running,
        "pid": pid,
        "machine": {"id": store.machine["id"], "name": store.machine["name"]},
        "data_dir": str(store.data_dir),
        "report_dir": str(store.report_dir),
        "config_path": str(CONFIG_PATH),
        "days": len(days),
        "first_day": days[0].isoformat() if days else None,
        "last_day": days[-1].isoformat() if days else None,
        "today_window": s["window"],
        "today_idle": s["idle"],
        "today_apps": len(s["apps"]),
        "server_time": dt.datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "timelog"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音，别刷屏
        pass

    # ---- 工具 ----

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---- 路由 ----

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        store = Store()
        try:
            if u.path in ("/", "/index.html"):
                page = UI_DIR / "index.html"
                if not page.exists():
                    return self._json({"error": "前端文件缺失"}, 404)
                return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/status":
                return self._json(status_payload(store))
            if u.path == "/api/day":
                date = (q.get("date") or [dt.date.today().isoformat()])[0]
                try:
                    day = dt.date.fromisoformat(date)
                except ValueError:
                    return self._json({"error": "日期格式不对"}, 400)
                return self._json(day_payload(store, day))
            if u.path == "/api/week":
                now = dt.date.today()
                if q.get("week"):
                    iso = now.isocalendar()
                    jan4 = dt.date(iso.year, 1, 4)
                    start = jan4 - dt.timedelta(days=jan4.weekday()) + dt.timedelta(
                        weeks=int(q["week"][0]) - 1
                    )
                else:
                    start = now - dt.timedelta(days=now.weekday())
                return self._json(week_payload(store, start))
            if u.path == "/api/report":
                kind = (q.get("kind") or ["daily"])[0]
                now = dt.datetime.now()
                if kind == "daily":
                    date = (q.get("date") or [now.date().isoformat()])[0]
                    day = dt.date.fromisoformat(date)
                    segs = R.segments_for_range(store, day, day)
                    manual = R.manual_entries(store, day, day)
                    md = R.render_daily(store, day, segs, manual, now)
                    path = store.report_dir / f"daily-{day.isoformat()}.md"
                else:
                    if q.get("week"):
                        iso = now.isocalendar()
                        jan4 = dt.date(iso.year, 1, 4)
                        start = jan4 - dt.timedelta(days=jan4.weekday()) + dt.timedelta(
                            weeks=int(q["week"][0]) - 1
                        )
                    else:
                        start = now.date() - dt.timedelta(days=now.date().weekday())
                    start = start - dt.timedelta(days=start.weekday())
                    end = start + dt.timedelta(days=6)
                    manual = R.manual_entries(store, start, end)
                    md = R.render_weekly(store, start, end, manual, now)
                    path = store.report_dir / f"weekly-{start.isocalendar()[1]:02d}-{start.isocalendar()[0]}.md"
                path.write_text(md, encoding="utf-8")
                return self._json({"path": str(path), "markdown": md})
            if u.path == "/api/config":
                return self._json({"path": str(CONFIG_PATH), "config": load_config()})
            return self._json({"error": "没这个接口"}, 404)
        except Exception as exc:  # 任何异常都要回 JSON，前端才不会白屏
            import traceback

            return self._json(
                {"error": str(exc), "trace": traceback.format_exc()[-1200:]}, 500
            )

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        body = self._body()
        store = Store()
        try:
            if u.path == "/api/add":
                note = str(body.get("note", "")).strip()
                if not note:
                    return self._json({"error": "内容不能为空"}, 400)
                dur = parse_duration(str(body["dur"]).strip()) if body.get("dur") else None
                when = parse_when(body.get("at") or None)
                store.append_manual(note, when, dur)
                return self._json(
                    {
                        "ok": True,
                        "note": note,
                        "dur": dur,
                        "at": when.isoformat(timespec="seconds"),
                    }
                )
            if u.path == "/api/collector/start":
                ok, msg = do_start(store, load_config(), bg=True)
                return self._json({"ok": ok, "message": msg}, 200 if ok else 400)
            if u.path == "/api/collector/stop":
                ok, msg = do_stop(store)
                return self._json({"ok": ok, "message": msg}, 200 if ok else 400)
            if u.path == "/api/config":
                cfg = load_config()
                patch = body.get("config") or {}
                if not isinstance(patch, dict):
                    return self._json({"error": "config 得是对象"}, 400)
                cfg.update(patch)
                save_config(cfg)
                return self._json({"ok": True, "config": cfg})
            return self._json({"error": "没这个接口"}, 404)
        except Exception as exc:
            import traceback

            return self._json(
                {"error": str(exc), "trace": traceback.format_exc()[-1200:]}, 500
            )


# ---------------------------------------------------------------- 启动


def probe(port: int) -> bool:
    """这个端口上是不是已经有我们的看板在跑。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("app") == APP_TAG
    except Exception:
        return False


def open_browser(url: str) -> None:
    try:
        os.startfile(url)  # noqa: S606  Windows 上最稳
    except Exception:
        try:
            webbrowser.open(url)
        except Exception:
            pass


def pick_port(preferred: int = DEFAULT_PORT) -> int:
    for port in (preferred, preferred + 1, preferred + 2, 0):
        if port == 0:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    port = DEFAULT_PORT
    if "--port" in argv:
        i = argv.index("--port")
        try:
            port = int(argv[i + 1])
        except (IndexError, ValueError):
            pass
    no_browser = "--no-browser" in argv

    try:
        return _serve(port, no_browser)
    except Exception:
        # 用 pythonw 启动时没有控制台，出错只能写文件
        log = BASE_DIR / "data" / "web.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            import traceback

            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n--- {dt.datetime.now().isoformat(timespec='seconds')} ---\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        return 1


def _serve(port: int, no_browser: bool) -> int:
    if probe(port):
        print(f"看板已经在运行：http://127.0.0.1:{port}")
        if not no_browser:
            open_browser(f"http://127.0.0.1:{port}")
        return 0

    port = pick_port(port)
    url = f"http://127.0.0.1:{port}"
    print(f"时间记录看板：{url}")
    print("关闭这个命令行窗口即可停止看板（数据采集不受影响）")

    # 记下自己的 pid，更新代码后要能把它重启掉
    pid_file = BASE_DIR / "data" / "web.pid"
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pid_file = None

    if not no_browser:
        threading_timer = __import__("threading").Timer(0.8, open_browser, args=(url,))
        threading_timer.daemon = True
        threading_timer.start()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"端口 {port} 起不来：{exc}")
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n看板已停止")
    finally:
        server.server_close()
        if pid_file and pid_file.exists():
            try:
                pid_file.unlink()
            except OSError:
                pass
    return 0


def restart_dashboard(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """把正在跑的看板停掉（更新代码后用）。"""
    pid_file = BASE_DIR / "data" / "web.pid"
    killed = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 15)
            killed = True
            time.sleep(1)
        except Exception:
            pass
        try:
            pid_file.unlink()
        except OSError:
            pass
    return killed, "旧看板已停" if killed else "没有正在运行的看板"


if __name__ == "__main__":
    sys.exit(main())
