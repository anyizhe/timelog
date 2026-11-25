"""API 通道的同步往返自测（真调 GitHub API）。

用账号下一个固定的私有测试仓 `timelog-selftest-tmp`，每次跑用**唯一机器号**，
所以不会和上次的残留数据互相干扰。

注意：GitHub 的 DELETE 接口（删文件/删仓）在这台机器上被网络代理挡掉
（返回 500/502 空响应；关掉代理时仓库删除又会因缺 delete_repo 权限返回 403），
所以脚本删不干净测试仓，**需要手动在网页上删一次**：
    https://github.com/anyizhe/timelog-selftest-tmp/settings

    python tests/selftest_api.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import uuid
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
    code, me = sync_api.call("GET", "/user", tok)
    if code != 200:
        print(f"认证失败：{me}")
        return 1
    login = me["login"]
    print(f"账号：{login}")

    print("\n[1] 确保测试仓存在")
    code, repo = sync_api.call_retry("POST", "/user/repos", tok, {
        "name": REPO_NAME, "private": True, "description": "timelog 自测临时仓", "auto_init": False,
    })
    if code != 201:
        code2, repo = sync_api.call("GET", f"/repos/{login}/{REPO_NAME}", tok)
        if code2 != 200:
            print(f"建仓失败：{code} {repo}")
            return 1
        print("  （仓已存在，沿用）")
    slug = f"{login}/{REPO_NAME}"
    repo_url = repo["clone_url"]
    print(f"  ✓ {slug}")

    # 每次用唯一机器号，避免和历史残留撞名
    tag = uuid.uuid4().hex[:6]
    mid_a, mid_b = f"a{tag}", f"b{tag}"

    import sync
    from store import Store

    # 用固定的日期，别用 date.today()/now —— 测试正好跨午夜时会对不上
    day = dt.date(2026, 11, 20)
    day_str = day.isoformat()
    file_a = f"{day_str}.{mid_a}.jsonl"
    file_b = f"{day_str}.{mid_b}.jsonl"

    try:
        print("\n[2] 机器 A 写数据并推上去")
        use_machine(1, "台式机", mid_a)
        storeA = Store()
        # 要有真实的时间跨度：同一时刻的两个事件时长为 0，会被时间线过滤掉
        now = dt.datetime.combine(day, dt.time(10, 0))
        storeA.append({"kind": "window", "app": "Code.exe", "title": "A 干的活"}, now)
        storeA.append({"kind": "end"}, now + dt.timedelta(hours=1))
        os.environ["TIMELOG_SYNC_FILE"] = str(SB / "m1" / "sync.json")
        sync.save_sync_config({"repo": repo_url, "transport": "api"})
        ok, msg = sync_api.run(storeA)
        check("A 推送成功", ok, msg[-400:])
        check("A 有上传动作", "上传 1 个文件" in msg, msg)

        code, listing = sync_api.call("GET", f"/repos/{slug}/contents/", tok)
        names = [x["name"] for x in listing] if code == 200 else []
        check("远端有 A 的数据文件", file_a in names, str(names[-5:]))

        print("\n[3] 机器 B 拉下来")
        use_machine(2, "笔记本", mid_b)
        storeB = Store()
        os.environ["TIMELOG_SYNC_FILE"] = str(SB / "m2" / "sync.json")
        sync.save_sync_config({"repo": repo_url, "transport": "api"})
        ok, msg = sync_api.run(storeB)
        check("B 同步成功", ok, msg[-400:])
        got = list((SB / "m2" / "data").glob(f"*.{mid_a}.jsonl"))
        check("B 拉到了 A 的文件", len(got) == 1, f"实际 {[f.name for f in got]}")
        if got:
            check("文件内容完整", "A 干的活" in got[0].read_text(encoding="utf-8"), "")

        print("\n[4] B 写自己的再推，A 再拉")
        storeB.append({"kind": "window", "app": "Chrome.exe", "title": "B 干的活"},
                      dt.datetime.combine(day, dt.time(14, 0)))
        ok, msg = sync_api.run(storeB)
        check("B 推送成功", ok, msg[-400:])

        use_machine(1, "台式机", mid_a)
        storeA = Store()
        os.environ["TIMELOG_SYNC_FILE"] = str(SB / "m1" / "sync.json")
        ok, msg = sync_api.run(storeA)
        check("A 再同步成功", ok, msg[-400:])
        filesA = sorted(p.name for p in (SB / "m1" / "data").glob("*.jsonl"))
        check("A 收到了 B 的文件", file_b in filesA, str(filesA))
        check("A 两份文件都在", file_a in filesA and file_b in filesA, str(filesA))

        print("\n[5] 合并后的统计")
        from report import segments_for_range, summarize

        segs = segments_for_range(storeA, day, day)
        s = summarize(segs)
        check("合并后认得出两台机器", len(s["by_machine"]) >= 2, str(s["by_machine"]))
        print(f"  设备：{s['by_machine']}")
    finally:
        print("\n[6] 尽力清理（删接口常被网络挡掉，失败属正常）")
        code, listing = sync_api.call("GET", f"/repos/{slug}/contents/", tok)
        if code == 200:
            for x in listing:
                if x["name"].endswith(".jsonl") and (x["name"] == file_a or x["name"] == file_b):
                    sync_api.call("DELETE", f"/repos/{slug}/contents/{x['name']}", tok,
                                  {"message": "selftest cleanup", "sha": x["sha"]})
        code, res = sync_api.call_retry("DELETE", f"/repos/{slug}", tok)
        print(f"  删仓接口返回 {code}"
              + ("（这个接口在当前网络下不可用，测试仓需要手动在网页上删）"
                 if code not in (204, 404) else ""))

    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    shutil.rmtree(SB, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
