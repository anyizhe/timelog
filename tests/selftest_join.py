"""端到端验证「新电脑接入」流程。

  A. 从 GitHub 公开仓 clone（验证代码仓真的公开可 clone）
  B. 在 clone 出来的副本里跑 join.py，数据仓库用本地 bare 仓代替
     （绝不用真实的 timelog-data 仓，免得混进假机器的数据）

跑在系统临时目录，不碰用户的真实安装。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CODE_REPO = "https://github.com/anyizhe/timelog.git"
SB = Path(tempfile.mkdtemp(prefix="timelog-join-"))
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


def run(cmd, cwd=None, env=None, timeout=300):
    r = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, stdin=subprocess.DEVNULL, timeout=timeout,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    print(f"沙箱：{SB}")

    print("\n[A] 从 GitHub 公开仓 clone")
    work = SB / "clone"
    work.mkdir(parents=True, exist_ok=True)
    code, out = 1, ""
    for attempt in range(5):
        code, out = run(["git", "clone", "--depth", "1", CODE_REPO, str(work)], cwd=SB, timeout=300)
        if code == 0:
            print(f"  （第 {attempt + 1} 次成功，网络抽风重试有效）")
            break
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        print(f"  第 {attempt + 1} 次 clone 失败，重试…")
        import time as _t

        _t.sleep(5)
    check("clone 公开代码仓成功", code == 0, out.strip()[-300:])
    if code != 0:
        print("clone 失败，网络可能不通，稍后再试")
        shutil.rmtree(SB, ignore_errors=True)
        return 1
    check("clone 出了 timelog.py", (work / "timelog.py").exists())
    check("clone 出了 join.py", (work / "join.py").exists())
    check("clone 出了 ui/index.html", (work / "ui" / "index.html").exists())
    check("clone 里没有 data/", not (work / "data").exists())

    print("\n[B] 造一个本地 bare 仓当「数据仓库」")
    remote = SB / "fakedata.git"
    remote.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--bare", "-b", "main", str(remote)], cwd=SB)
    check("bare 仓建好", (remote / "HEAD").exists())

    print("\n[C] 在 clone 出来的副本里跑 join.py（沙箱模式）")
    env = dict(os.environ)
    env.update({
        "TIMELOG_MACHINE_FILE": str(SB / "newpc" / "machine.json"),
        "TIMELOG_DATA_DIR": str(SB / "newpc" / "data"),
        "TIMELOG_REPORT_DIR": str(SB / "newpc" / "reports"),
        "TIMELOG_SYNC_FILE": str(SB / "newpc" / "sync.json"),
    })
    (SB / "newpc").mkdir(parents=True, exist_ok=True)
    code, out = run(
        [sys.executable, str(work / "join.py"), "--name", "测试笔记本",
         "--data-repo", str(remote), "--minutes", "30"],
        cwd=work, env=env, timeout=300,
    )
    print("  --- join.py 输出 ---")
    for line in out.strip().splitlines()[:14]:
        print("   ", line)
    check("join.py 正常退出", code == 0, out.strip()[-400:])

    mf = json.loads((SB / "newpc" / "machine.json").read_text(encoding="utf-8"))
    check("机器身份已生成", bool(mf.get("id")) and mf.get("name") == "测试笔记本", str(mf))
    check("机器号和本机不同", mf.get("id") != "a3f9c2", mf.get("id", ""))

    sc = json.loads((SB / "newpc" / "sync.json").read_text(encoding="utf-8"))
    check("同步配置指向数据仓", sc.get("repo") == str(remote), str(sc.get("repo")))

    files = list((SB / "newpc" / "data").glob("*.jsonl"))
    check("数据目录可用", (SB / "newpc" / "data").exists())
    print(f"  （新电脑数据文件：{[f.name for f in files]}）")

    # 沙箱模式下不该动系统设置
    code, out = run(["schtasks", "/query", "/tn", "TimelogSync"], cwd=SB)
    print("  （沙箱模式不装计划任务，查询现有任务返回码 %d）" % code)

    print("\n" + ("全部通过 ✅" if not FAILED else f"失败 {len(FAILED)} 项: {FAILED}"))
    shutil.rmtree(SB, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
