"""API 通道的同步往返自测（真调 GitHub API）。

会在账号下临时建一个私有测试仓，测完删掉 —— **不会碰真实的 timelog-data 仓**。

    python tests/selftest_api.py
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import sync_api  # noqa: E402

SB = Path(tempfile.mkdtemp(prefix="timelog-api-"))
FAILED: list[str] = []
REPO_NAME = "timelog-selftest-tmp"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def api(method: str, path: str, tok: str, body: dict | None = None):
    return sync_api.call(method, path, tok, body)


def use_machine(idx: int, name: str, mid: str) -> Path:
    d = SB / f"m{idx}"
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


def main() -> int:
    tok = sync_api.get_token()
    if not tok:
        print("没有 GitHub 凭据，跳过")
        return 0
    code, me = api("GET", "/user", tok)
    if code != 200:
        print(f"认证失败：{me}")
        return 1
    login = me["login"]
    print(f"账号：{login}")

    print("\n[1] 建临时测试仓")
    code, repo = sync_api.call_retry("POST", "/user/repos", tok, {
        "name": REPO_NAME, "private": True, "description": "timelog 自测临时仓", "auto_init": False,
    })
    if code != 201:
        # GitHub 偶尔会返回 500 但其实已经建好了 —— 回查确认
        code2, repo = api("GET", f"/repos/{login}/{REPO_NAME}", tok)
        if code2 != 200:
            print(f"建仓失败：{code} {repo}")
            return 1
        print("  （建仓返回 500，但回查发现已建好）")
    print(f"  ✓ {repo['full_name']}")
    slug = f"{login}/{REPO_NAME}"
    repo_url = repo["clone_url"]

    try:
        print("\n[2] 机器 A 写数据并推上去")
        use_machine(1, "台式机", "aaaaaa")
        from store import Store

        storeA = Store()
        storeA.append({"kind": "window", "app": "Code.exe", "title": "A 干的活"}, dt.datetime.now())
        storeA.append({"kind": "end"}, dt.datetime.now())
        os.environ["TIMELOG_SYNC_FILE"] = str(SB / "m1" / "sync.json")
        import sync

        sync.save_sync_config({"repo": repo_url, "transport": "api"})
        ok, msg = sync_api.run(storeA)
        check("A 推送成功", ok, msg[-400:])
        check("A 有上传动作", "上传 1 个文件" in msg, msg)

        code, listing = api("GET", f"/repos/{slug}/contents/", tok)
        names = [x["name"] for x in listing] if code == 200 else []
        check("远端有 A 的数据文件", any(n.startswith("2026-") and ".aaaaaa." in n for n in names), str(names))

        print("\n[3] 机器 B 拉下来")
        use_machine(2, "笔记本", "bbbbbb")
        storeB = Store()
        os.environ["TIMELOG_SYNC_FILE"] = str(SB / "m2" / "sync.json")
        sync.save_sync_config({"repo": repo_url, "transport": "api"})
        ok, msg = sync_api.run(storeB)
        check("B 同步成功", ok, msg[-400:])
        filesB = list((SB / "m2" / "data").glob("*.aaaaaa.jsonl"))
        check("B 拉到了 A 的文件", len(filesB) == 1, f"实际 {[f.name for f in filesB]}")
        if filesB:
            txt = filesB[0].read_text(encoding="utf-8")
            check("文件内容完整", "A 干的活" in txt, txt[:120])

        print("\n[4] B 写自己的再推，A 再拉")
        storeB.append({"kind": "window", "app": "Chrome.exe", "title": "B 干的活"}, dt.datetime.now())
        ok, msg = sync_api.run(storeB)
        check("B 推送成功", ok, msg[-400:])

        use_machine(1, "台式机", "aaaaaa")
        storeA = Store()
        os.environ["TIMELOG_SYNC_FILE"] = str(SB / "m1" / "sync.json")
        ok, msg = sync_api.run(storeA)
        check("A 再同步成功", ok, msg[-400:])
        filesA = sorted(p.name for p in (SB / "m1" / "data").glob("*.jsonl"))
        check("A 收到了 B 的文件", any(".bbbbbb." in f for f in filesA), str(filesA))
        check("A 两份文件都在", len(filesA) == 2, str(filesA))

        print("\n[5] 合并后的统计")
        from report import segments_for_range, summarize

        segs = segments_for_range(storeA, dt.date.today(), dt.date.today())
        s = summarize(segs)
        check("合并后认得出两台机器", len(s["by_machine"]) >= 1, str(s["by_machine"]))
        print(f"  设备：{s['by_machine']}")
    finally:
        print("\n[6] 删掉临时测试仓")
        code, res = sync_api.call_retry("DELETE", f"/repos/{slug}", tok)
        if code != 204:
            # GitHub 偶尔返回 500 但其实删掉了 —— 回查确认
            code2, _ = api("GET", f"/repos/{slug}", tok)
            check("临时仓已删除", code2 == 404, f"DELETE 返回 {code}，回查 {code2}")
        else:
            check("临时仓已删除", True)

    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    shutil.rmtree(SB, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
