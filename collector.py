"""前台窗口采集器（纯 ctypes 调 Win32 API，不依赖第三方库）。

采样逻辑：
  - 每 poll_seconds 秒取一次前台窗口 → 进程名 + 窗口标题
  - 键鼠空闲超过 idle_seconds → 记为 idle 段（否则深夜没动鼠标会被算成"在用电脑"）
  - 状态没变化时每隔 heartbeat_seconds 补一条心跳，给同一活动封顶
  - 跨天：写新一天的第一条事件，并自动补生成昨天的日报（周一额外补上周周报）

事件里 kind 字段：
  window —— 普通应用活动
  idle   —— 空闲
  end    —— 监控停止标记，用来给最后一段封口
"""
from __future__ import annotations

import ctypes
import datetime as dt
import os
import sys
import time
from ctypes import wintypes

if not sys.platform.startswith("win"):
    raise RuntimeError("这个工具目前只支持 Windows（依赖 Win32 前台窗口 API）")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetLastInputInfo.restype = wintypes.BOOL
user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.GetTickCount.argtypes = []

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwTime", wintypes.DWORD)]


def _pid_to_name(pid: int) -> str:
    """进程 ID → 进程名（如 Code.exe）。拿不到就返回空串。"""
    if not pid:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            full = buf.value
            return full.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(h)


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = user32.GetClassNameW(hwnd, buf, 255)
    return buf.value if n > 0 else ""


def _window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    n = user32.GetWindowTextW(hwnd, buf, 1023)
    return buf.value if n > 0 else ""


def idle_seconds() -> float:
    """距离最后一次键鼠输入过了多少秒。"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    tick = kernel32.GetTickCount()
    # GetTickCount 是 32 位，会回绕，用无符号减法
    delta = (tick - lii.dwTime) & 0xFFFFFFFF
    return delta / 1000.0


def foreground() -> tuple[str, str, str]:
    """返回 (进程名, 窗口标题, 窗口类名)。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _pid_to_name(pid.value), _window_title(hwnd), _class_name(hwnd)


def _clean(text: str) -> str:
    return " ".join(str(text).replace("\r", " ").replace("\n", " ").split())


class Collector:
    def __init__(self, store, cfg: dict):
        self.store = store
        self.cfg = cfg
        self.poll = max(0.5, float(cfg.get("poll_seconds", 2)))
        self.heartbeat = max(10.0, float(cfg.get("heartbeat_seconds", 120)))
        self.idle_limit = max(30.0, float(cfg.get("idle_seconds", 600)))
        self.hide_kw = [k.lower() for k in cfg.get("hide_title_keywords", []) if k]
        self.ignore_apps = {a.lower() for a in cfg.get("ignore_apps", []) if a}
        self.hide_apps = {a.lower() for a in cfg.get("hide_apps", []) if a}
        self.skip_classes = {c.lower() for c in cfg.get("skip_window_classes", []) if c}
        self._last_key: tuple | None = None
        self._last_write = 0.0
        self._day = dt.date.today()

    # ---------- 采样 ----------

    def _apply_privacy(self, app: str, title: str) -> tuple[str, str]:
        app_l = app.lower()
        if app_l in self.ignore_apps:
            return "", ""
        title = _clean(title)
        if app_l in self.hide_apps:
            return app, "[已隐藏]"
        for kw in self.hide_kw:
            if kw and kw in title.lower():
                return app, "[已隐藏]"
        return app, title

    def sample(self) -> tuple[str, str, str]:
        """返回 (kind, app, title)。kind ∈ window / idle / none。"""
        app, title, cls = foreground()
        if cls.lower() in self.skip_classes:
            return "none", "", ""  # 系统窗口，不记
        app, title = self._apply_privacy(app, title)
        if not app:
            return "none", "", ""
        if idle_seconds() >= self.idle_limit:
            return "idle", "", ""
        return "window", app, title

    # ---------- 事件 ----------

    def _write(self, kind: str, app: str, title: str, when: dt.datetime) -> None:
        if kind == "window":
            self.store.append({"kind": "window", "app": app, "title": title}, when)
        elif kind == "idle":
            self.store.append({"kind": "idle", "app": "", "title": ""}, when)
        else:  # none —— 没有有效前台窗口，只用来给上一段封口，不进报告
            self.store.append({"kind": "none", "app": "", "title": ""}, when)
        self._last_key = (kind, app, title)
        self._last_write = time.time()

    def _rollover(self, now: dt.datetime) -> None:
        """跨天处理：补生成昨天日报；周一再补上周周报。"""
        prev_day = self._day
        self._day = now.date()
        if not self.cfg.get("auto_report", True):
            return
        try:
            from report import generate_daily_report, generate_weekly_report

            generate_daily_report(self.store, prev_day)
            if now.weekday() == 0:  # 周一 → 补上一周
                last_week_end = prev_day  # 周日
                last_week_start = last_week_end - dt.timedelta(days=6)
                generate_weekly_report(self.store, last_week_start)
        except Exception as exc:  # 报告失败不能影响采集
            print(f"[timelog] 自动生成报告失败: {exc}", flush=True)

    # ---------- 主循环 ----------

    def run(self) -> None:
        print(f"[timelog] 开始监控（每 {self.poll:g}s 采样，空闲判定 {self.idle_limit:g}s）", flush=True)
        print(f"[timelog] 数据目录: {self.store.data_dir}", flush=True)
        now = dt.datetime.now()
        self._day = now.date()
        # 会话开始标记：万一上次是异常退出（没来得及写 end 标记），
        # 这条标记会把中间的空档切开，空档不能算进上一段活动。
        self.store.append({"kind": "none", "app": "", "title": "__session_start__"}, now)
        # 启动再记一条，把当天的起点钉住
        kind, app, title = self.sample()
        self._write(kind, app, title, now)
        last_day = self._day

        try:
            while True:
                if self.store.stop_flag.exists():
                    print("[timelog] 收到停止指令", flush=True)
                    break
                time.sleep(self.poll)
                now = dt.datetime.now()
                if now.date() != last_day:
                    self._rollover(now)
                    last_day = now.date()
                    kind, app, title = self.sample()
                    self._write(kind, app, title, now)
                    continue
                kind, app, title = self.sample()
                key = (kind, app, title)
                if key != self._last_key:
                    self._write(kind, app, title, now)
                elif time.time() - self._last_write >= self.heartbeat:
                    self._write(kind, app, title, now)
        except KeyboardInterrupt:
            print("[timelog] 收到 Ctrl+C", flush=True)
        finally:
            # 封口：让最后一段活动有明确终点
            try:
                self.store.append(
                    {"kind": "end", "app": "", "title": ""}, dt.datetime.now()
                )
            except Exception:
                pass
            print("[timelog] 监控已停止", flush=True)
