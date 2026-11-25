"""跨机器数据同步：GitHub 私有数据仓。

思路：
  - 数据文件名带机器号（`2026-11-25.a3f9c2.jsonl`），两台电脑推同一个仓库
    **永远不会撞文件名**，git 不需要任何冲突处理。
  - 本地 `data/` 目录是"工作区"，数据仓库是"中转站"。
    sync 做三件事：本地 → 仓库 → 拉回别人机器的文件 → 重算报告 → 提交推送。

命令：
    python timelog.py sync setup <仓库地址>   首次配置
    python timelog.py sync run                同步一次（拉取 + 推送 + 重算报告）
    python timelog.py sync status             看状态
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _sync_file() -> Path:
    # 环境变量可覆盖（自测要模拟两台机器各自独立的同步配置）
    env = os.environ.get("TIMELOG_SYNC_FILE")
    return Path(env) if env else BASE_DIR / "sync.json"


DEFAULT_REPO_DIR = BASE_DIR.parent / "timelog-data"

# 只同步数据文件，pid/log/flag 这些运行时文件留在本地
DATA_GLOB = "*.jsonl"


# ---------------------------------------------------------------- 配置


def load_sync_config() -> dict:
    p = _sync_file()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_sync_config(cfg: dict) -> Path:
    p = _sync_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def repo_dir() -> Path:
    cfg = load_sync_config()
    p = cfg.get("path")
    return Path(p) if p else DEFAULT_REPO_DIR


# ---------------------------------------------------------------- git 封装


def git(repo: Path, *args: str, check: bool = False, timeout: int = 90) -> tuple[int, str]:
    """跑一条 git 命令。

    stdin 一定要接 DEVNULL —— 否则 git 遇到需要输入的提示（要用户名/密码）会
    一直等终端输入，表现为整个同步卡死。认证交给 Git Credential Manager 自己处理。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失败：\n{out.strip()}")
        return r.returncode, out
    except FileNotFoundError:
        return 127, "找不到 git，请先安装 Git for Windows"
    except subprocess.TimeoutExpired:
        return 124, f"git {' '.join(args)} 超时（{timeout}秒）——网络不通或被卡住了"


def git_ok(repo: Path, *args: str) -> bool:
    code, _ = git(repo, *args)
    return code == 0


def git_retry(repo: Path, *args: str, tries: int = 3, timeout: int = 90) -> tuple[int, str, int]:
    """网络抽风时重试几次。返回 (退出码, 输出, 实际尝试次数)。"""
    code, out = 1, ""
    for i in range(tries):
        code, out = git(repo, *args, timeout=timeout)
        if code == 0:
            return 0, out, i + 1
        if i < tries - 1:
            time.sleep(3 * (i + 1))
    return code, out, tries


# ---------------------------------------------------------------- 事件合并


def _event_key(line: str) -> tuple:
    try:
        r = json.loads(line)
    except Exception:
        return (line,)
    return (
        round(float(r.get("t", 0)), 3),
        r.get("kind", ""),
        r.get("app", ""),
        r.get("title", ""),
        r.get("note", ""),
        r.get("m", ""),
    )


def merge_jsonl(local: Path, remote: Path) -> int:
    """把 remote 里本地没有的事件并进 local（按事件身份去重）。返回新增条数。"""
    seen: set[tuple] = set()
    out_lines: list[str] = []
    for p in (local, remote):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            k = _event_key(line)
            if k in seen:
                continue
            seen.add(k)
            out_lines.append(line)
    out_lines.sort(key=lambda ln: json.loads(ln).get("t", 0))
    before = len(local.read_text(encoding="utf-8").splitlines()) if local.exists() else 0
    local.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    return len(out_lines) - before


# ---------------------------------------------------------------- 主流程


def setup(url: str | None = None, repo_path: str | None = None, transport: str | None = None) -> tuple[bool, str]:
    cfg = load_sync_config()
    if url:
        cfg["repo"] = url.strip()
    if repo_path:
        cfg["path"] = str(Path(repo_path).expanduser().resolve())
    cfg["transport"] = (transport or cfg.get("transport") or "git").lower()
    save_sync_config(cfg)

    if not cfg.get("repo"):
        return False, (
            "还没指定仓库地址。\n"
            "  1) 在 GitHub 上新建一个 **Private** 仓库（不要勾 README/LICENSE），名字比如 timelog-data\n"
            "  2) 把它的地址发我，或自己跑：\n"
            "     python timelog.py sync setup https://github.com/<你的用户名>/timelog-data.git"
        )

    if cfg["transport"] != "git":
        # API 通道：不需要本地 git 仓库，也不用先 clone
        return True, (
            f"数据仓库已配置（API 通道）：{cfg['repo']}\n\n"
            "下一步：\n"
            "  python timelog.py sync run\n\n"
            "API 通道不需要本地 git 仓库，网络抽风时更耐打。\n"
            "两台电脑都跑一次 sync setup + sync run，之后每天 sync run 一次即可。"
        )

    repo = repo_dir()
    cfg["path"] = str(repo)
    save_sync_config(cfg)
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        code, out = git(repo, "init", "-b", "main")
        if code != 0:
            code, out = git(repo, "init")
            git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    git_ok(repo, "symbolic-ref", "HEAD", "refs/heads/main")

    # 网络不稳时别死等：低速就放弃，下一轮再试
    git(repo, "config", "http.lowSpeedLimit", "1000")
    git(repo, "config", "http.lowSpeedTime", "30")
    git(repo, "config", "http.postBuffer", "524288000")
    git(repo, "config", "core.sshCommand", "ssh -o ConnectTimeout=15")

    # 数据仓库只要能提交就行，不强制用 GitHub 身份（代码仓才需要）
    code, name = git(repo, "config", "user.name")
    if code != 0 or not name.strip():
        git(repo, "config", "user.name", "timelog")
        git(repo, "config", "user.email", "timelog@localhost")
        print("· 数据仓库用本地身份提交（timelog <timelog@localhost>）")

    # 注意：不能用 `git remote` 判断远端是否存在 —— 没有远端时它也返回 0 且输出为空
    code, _ = git(repo, "remote", "get-url", "origin")
    if code == 0:
        git(repo, "remote", "set-url", "origin", cfg["repo"])
    else:
        git(repo, "remote", "add", "origin", cfg["repo"])

    keep = repo / ".gitignore"
    want = "*\n!.gitignore\n!README.md\n!*.jsonl\n"
    # 注意：放行规则必须放在最后，且不能有把 jsonl 重新忽略掉的规则
    # （.gitignore 里后面的规则优先，写成 `*.jsonl` 会把 `!*.jsonl` 覆盖掉，
    #   结果就是数据文件永远不被提交 —— 这坑踩过）
    if not keep.exists() or keep.read_text(encoding="utf-8").strip() != want.strip():
        keep.write_text(want, encoding="utf-8")
        print("· 修正了数据仓库的 .gitignore（放行 *.jsonl）")
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# timelog-data\n\n"
            "这个仓库是 timelog 的**数据仓库**，存放各台电脑的活动记录。\n\n"
            "- 每个文件是一天的数据，文件名带机器号（`2026-11-25.a3f9c2.jsonl`）\n"
            "- 里面的内容包含窗口标题，**请保持仓库私有**\n"
            "- 由 `timelog.py sync` 自动维护，手动改容易冲突\n",
            encoding="utf-8",
        )

    code, out = git(repo, "remote", "-v")
    return True, (
        f"数据仓库已就绪：{repo}\n"
        f"远端：{cfg['repo']}\n\n"
        "下一步：\n"
        "  1) 确认 GitHub 上已经建好这个**私有**仓库（空的，没有 README）\n"
        "  2) 跑一次首次推送（会在浏览器里让你登录 GitHub）：\n"
        "     python timelog.py sync run\n\n"
        "两台电脑都跑一次 sync setup + sync run，之后每天 sync run 一次即可。"
    )


def run(store, dry_run: bool = False, quiet: bool = False) -> tuple[bool, str]:
    cfg = load_sync_config()
    transport = (cfg.get("transport") or "git").lower()

    if transport == "api":
        import sync_api

        ok, msg = sync_api.run(store, dry_run)
        _log(store, msg, ok)
        return ok, msg

    if not cfg.get("repo"):
        return False, "还没配置数据仓库。先跑：python timelog.py sync setup <仓库地址>"
    repo = repo_dir()
    if not (repo / ".git").exists():
        # 本地没有 git 仓库就直接走 API 通道，不用非得先 clone
        import sync_api

        ok, msg = sync_api.run(store, dry_run)
        _log(store, msg, ok)
        return ok, msg

    # 防止两次同步撞车（计划任务 + 手动同时跑）
    lock = store.data_dir / "sync.lock"
    if lock.exists():
        try:
            age = time.time() - lock.stat().st_mtime
            if age < 900:
                return True, f"上一次同步还没结束（{int(age)} 秒前开始），这次跳过"
        except OSError:
            pass
    try:
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        lock = None

    try:
        ok, msg = _run_locked(store, repo, cfg, dry_run)
    finally:
        if lock and lock.exists():
            try:
                lock.unlink()
            except OSError:
                pass

    # git 通道因网络失败 → 自动降级走 API（国内直连 github.com 的 git 端口不稳）
    if not ok and not dry_run:
        import sync_api

        ok2, msg2 = sync_api.run(store, dry_run)
        if ok2:
            msg = msg + "\n\n—— git 通道没走通，已自动改用 API 通道完成同步 ——\n" + msg2
            ok = True

    _log(store, msg, ok)
    if quiet:
        return ok, msg
    return ok, msg


def _log(store, msg: str, ok: bool) -> None:
    """写一份文件日志 —— 计划任务跑的时候没有控制台，得靠它排查。"""
    try:
        p = store.data_dir / "sync.log"
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"--- {stamp} {'成功' if ok else '失败'} ---\n{msg}\n")
    except OSError:
        pass


def _run_locked(store, repo, cfg, dry_run: bool) -> tuple[bool, str]:

    lines: list[str] = []
    mine_id = store.machine["id"]

    # 1) 本地 → 仓库
    local_files = sorted(store.data_dir.glob(DATA_GLOB))
    copied = 0
    for f in local_files:
        dst = repo / f.name
        if not dst.exists() or dst.read_bytes() != f.read_bytes():
            if not dry_run:
                shutil.copy2(f, dst)
            copied += 1
    lines.append(f"本地 → 仓库：{copied} 个文件有变化")

    # 2) 提交本地改动
    if not dry_run:
        git(repo, "add", "-A")
        code, out = git(repo, "commit", "-m", f"sync {dt.datetime.now():%Y-%m-%d %H:%M}")
        if code != 0 and "nothing to commit" not in out.lower():
            lines.append(f"提交时有点小问题（可忽略）：{out.strip().splitlines()[-1] if out.strip() else ''}")

    # 3) 拉取别人的数据
    code, out, tries = git_retry(repo, "pull", "--rebase", "--autostash")
    pulled = "Already up to date" in out
    if tries > 1:
        lines.append(f"拉取远端：网络不稳，重试了 {tries - 1} 次才成功")
    else:
        lines.append(f"拉取远端：{'没有新东西' if pulled else '拉到更新'}")

    # 4) 仓库 → 本地（自己那份做并集合并，不丢数据）
    received = 0
    for f in sorted(repo.glob(DATA_GLOB)):
        dst = store.data_dir / f.name
        if f.name.endswith(f".{mine_id}.jsonl"):
            if dst.exists() and not dry_run:
                received += merge_jsonl(dst, f)
            elif not dst.exists() and not dry_run:
                shutil.copy2(f, dst)
                received += 1
        else:
            if not dst.exists() or dst.read_bytes() != f.read_bytes():
                if not dry_run:
                    shutil.copy2(f, dst)
                received += 1
    lines.append(f"仓库 → 本地：{received} 处更新")

    # 5) 重算受影响的报告
    days = store.available_days()
    recent = [d for d in days if d >= dt.date.today() - dt.timedelta(days=60)]
    regen = 0
    if not dry_run:
        from report import generate_daily_report, generate_weekly_report

        for d in recent:
            try:
                generate_daily_report(store, d)
                regen += 1
            except Exception:
                pass
        monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
        for k in range(4):
            try:
                generate_weekly_report(store, monday - dt.timedelta(days=7 * k))
            except Exception:
                pass
    lines.append(f"重新生成报告：{regen} 份日报")

    # 6) 推送
    if dry_run:
        lines.append("（--dry-run：没有真的提交和推送）")
        return True, "\n".join(lines)

    git(repo, "add", "-A")
    code, out = git(repo, "commit", "-m", f"merge {dt.datetime.now():%Y-%m-%d %H:%M}")
    if code != 0 and "nothing to commit" not in out.lower():
        lines.append(f"提交时有点小问题：{out.strip().splitlines()[-1] if out.strip() else ''}")
    code, out, tries = git_retry(repo, "push", "-u", "origin", "main")
    if code != 0:
        msg = out.strip()
        if "could not read Username" in msg or "Authentication failed" in msg or "403" in msg:
            lines.append(
                "推送失败：还没登录 GitHub。\n"
                "  浏览器会弹出登录页，登录一次就好（Git Credential Manager 会记住）。\n"
                f"  详细信息：{msg.splitlines()[-1] if msg else ''}"
            )
        else:
            lines.append(f"推送失败：{msg.splitlines()[-1] if msg else '未知错误'}")
        return False, "\n".join(lines)
    lines.append("推送：完成 ✓" + (f"（重试了 {tries - 1} 次）" if tries > 1 else ""))

    cfg["last_sync"] = dt.datetime.now().isoformat(timespec="seconds")
    save_sync_config(cfg)
    return True, "\n".join(lines)


def status(store) -> str:
    cfg = load_sync_config()
    repo = repo_dir()
    lines = [
        f"本机：{store.machine['name']}（id {store.machine['id']}）",
        f"数据目录：{store.data_dir}",
        f"数据仓库：{repo}" + ("" if (repo / ".git").exists() else "　（还没初始化）"),
        f"远端：{cfg.get('repo') or '（未配置）'}",
        f"上次同步：{cfg.get('last_sync') or '（还没同步过）'}",
        "",
    ]
    days = store.available_days()
    if not days:
        lines.append("还没有任何数据。")
        return "\n".join(lines)
    lines.append("各机器的数据：")
    by_machine: dict[str, list[str]] = {}
    for f in sorted(store.data_dir.glob(DATA_GLOB)):
        parts = f.stem.split(".")
        name = parts[1] if len(parts) > 1 else "(旧格式)"
        by_machine.setdefault(name, []).append(f.name)
    for name, files in sorted(by_machine.items()):
        lines.append(f"  {name}：{len(files)} 天（{files[0].split('.')[0]} ~ {files[-1].split('.')[0]}）")
    lines.append("")
    lines.append("自动同步：" + task_status_text())
    return "\n".join(lines)


# ---------------------------------------------------------------- 自动同步（计划任务）


TASK_MINUTE = "TimelogSync"
TASK_LOGON = "TimelogSyncAtLogon"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "TimelogSyncAtLogon"


def _pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


def _task_cmd() -> str:
    """计划任务要执行的命令。用 pythonw 免得每 30 分钟闪一个黑框。"""
    return f'cmd /c ""{_pythonw()}" "{BASE_DIR / "timelog.py"}" sync run --quiet"'


def _schtasks(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["schtasks", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=60,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "找不到 schtasks"
    except subprocess.TimeoutExpired:
        return 124, "schtasks 超时"


def _task_exists(name: str) -> bool:
    code, _ = _schtasks("/query", "/tn", name)
    return code == 0


def install_task(minutes: int = 30) -> tuple[bool, str]:
    """装两个自动同步触发器：每隔 N 分钟同步一次 + 每次登录时同步一次。

    「每 N 分钟」用计划任务（schtasks）；
    「登录时」用注册表 Run 键 —— 因为 schtasks 的 onlogon 触发器要管理员权限，
    普通用户会被 "Access is denied" 拒掉，而注册表 HKCU 不需要提权。
    """
    cmd = _task_cmd()
    out = []
    code, msg = _schtasks(
        "/create", "/tn", TASK_MINUTE, "/tr", cmd, "/sc", "minute", "/mo", str(minutes), "/f"
    )
    if code != 0:
        return False, f"建每 {minutes} 分钟的任务失败：\n{msg.strip()}"
    out.append(f"✓ 每 {minutes} 分钟同步一次（计划任务 {TASK_MINUTE}）")

    code, msg = _reg_run_set(RUN_NAME, cmd)
    if code == 0:
        out.append(f"✓ 每次登录时同步一次（注册表 Run\\{RUN_NAME}）")
    else:
        out.append(f"（登录时同步那个没建成：{msg.strip()[:120]}）")

    out.append("")
    out.append("触发时执行的命令：")
    out.append(f"  {cmd}")
    out.append("")
    out.append("卸载：python timelog.py sync autostart --mode remove")
    return True, "\n".join(out)


def _reg_run_set(name: str, cmd: str) -> tuple[int, str]:
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS)
        with key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
        return 0, ""
    except Exception as exc:
        return 1, str(exc)


def _reg_run_del(name: str) -> tuple[int, str]:
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS)
        with key:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        return 0, ""
    except Exception as exc:
        return 1, str(exc)


def _reg_run_exists(name: str) -> bool:
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        with key:
            winreg.QueryValueEx(key, name)
        return True
    except Exception:
        return False


def remove_task() -> tuple[bool, str]:
    out = []
    for name in (TASK_MINUTE, TASK_LOGON):
        if _task_exists(name):
            code, msg = _schtasks("/delete", "/tn", name, "/f")
            if code == 0:
                out.append(f"✓ 已删除计划任务 {name}")
            else:
                out.append(f"✗ 删除 {name} 失败：{msg.strip()[:120]}")
        else:
            out.append(f"· 计划任务 {name} 本来就没有")
    code, msg = _reg_run_del(RUN_NAME)
    out.append("✓ 已删除注册表登录触发器" if code == 0 else f"· 删除注册表项失败：{msg[:100]}")
    return True, "\n".join(out)


def task_status_text() -> str:
    parts = []
    for name in (TASK_MINUTE,):
        parts.append(f"每 N 分钟同步：{'已装' if _task_exists(name) else '未装'}")
    parts.append(f"登录时同步：{'已装' if _reg_run_exists(RUN_NAME) else '未装'}")
    return "，".join(parts)
