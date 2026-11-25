"""机器身份：每台电脑一个稳定 id 和名字。

存在 machine.json（本地文件，不进 git）。每条事件都带上这两个字段，
这样两台电脑的数据合并到一块时，能知道每段是「哪台机器」上发生的。
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _machine_file() -> Path:
    # 环境变量可覆盖（自测要在一个进程里模拟两台机器）
    env = os.environ.get("TIMELOG_MACHINE_FILE")
    return Path(env) if env else BASE_DIR / "machine.json"


def default_name() -> str:
    try:
        host = socket.gethostname().strip()
    except Exception:
        host = ""
    return host or "这台电脑"


def save_machine(info: dict) -> Path:
    p = _machine_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def load_machine() -> dict:
    """读取机器身份；没有就生成一个并落盘。"""
    p = _machine_file()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("id") and data.get("name"):
                return {"id": str(data["id"]), "name": str(data["name"])}
        except Exception:
            pass
    info = {"id": uuid.uuid4().hex[:6], "name": default_name()}
    save_machine(info)
    return info


def machine_id() -> str:
    return load_machine()["id"]


def machine_name() -> str:
    return load_machine()["name"]


def rename(new_name: str) -> dict:
    info = load_machine()
    info["name"] = new_name.strip() or info["name"]
    save_machine(info)
    return info
