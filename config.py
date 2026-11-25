"""配置：默认值 + config.json 覆盖。

config.json 位于项目根目录，用户可直接编辑；不存在时用默认值并自动生成一份。
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    # 采样间隔（秒）：轮询前台窗口的频率
    "poll_seconds": 2,
    # 心跳间隔（秒）：状态没变也定期补一条，用来给「同一活动」封顶，防止最后一段无限延长
    "heartbeat_seconds": 120,
    # 空闲判定（秒）：超过这个时间没有任何键鼠输入，认为人不在
    "idle_seconds": 600,
    # 标题里命中这些关键词就整条隐藏（大小写不敏感）
    "hide_title_keywords": [
        "password", "passwd", "pwd", "secret", "token", "cookie",
        "密码", "验证码", "身份证", "银行卡", "私钥",
    ],
    # 这些进程完全不记录（进程名，小写不带 .exe）
    "ignore_apps": [],
    # 这些进程只记录时长，隐藏窗口标题
    "hide_apps": [],
    # 这些窗口类名直接跳过（桌面、任务栏、输入法等系统窗口）
    "skip_window_classes": [
        "progman", "shelldlldefview", "shelltraywnd", "workerw",
        "windows.ui.core.corewindow", "windows.ui.composition.desktopwindowcontentbridge",
        "default ime", "msctfime ui", "ime", "notifyiconoverflowwindow",
        "tasklistthumbnail", "multitaskingviewframe",
    ],
    # 停止监控时顺手生成当天报告
    "report_on_stop": True,
    # 自动生成报告：跨天时补昨天，周一那天补上一周
    "auto_report": True,
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """读取配置；config.json 存在则覆盖默认值。"""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                cfg = _deep_merge(cfg, raw)
        except Exception:
            # 配置坏了不该让监控崩掉，退回默认值
            pass
    return cfg


def ensure_config_file() -> Path:
    """首次运行时写一份带注释说明的 config.json。"""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return CONFIG_PATH


def save_config(cfg: dict) -> Path:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return CONFIG_PATH
