"""桌面快捷方式（用 PowerShell 的 WScript.Shell COM，不依赖 pywin32）。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$pyw = '{pyw}'
if (-not (Test-Path $pyw)) {{
  $c = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($c) {{ $pyw = $c.Source }}
}}
if (-not $pyw) {{ Write-Output 'NO_PYTHONW'; exit 1 }}
$ws = New-Object -ComObject WScript.Shell
$lnk = Join-Path $desktop '{name}'
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $pyw
$sc.Arguments = '"{script}"'
$sc.WorkingDirectory = '{workdir}'
$sc.IconLocation = '{icon},0'
$sc.Description = '{desc}'
$sc.WindowStyle = 7
$sc.Save()
Write-Output "OK:$lnk"
"""


def pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


def create_desktop_shortcut(
    name: str = "时间记录看板.lnk",
    script: str = "web.py",
    desc: str = "打开时间记录看板（本地网页）",
    icon: str | None = None,
) -> tuple[bool, str]:
    """在桌面建一个快捷方式。返回 (是否成功, 说明)。"""
    ps = PS_TEMPLATE.format(
        pyw=pythonw(),
        name=name,
        script=str(BASE_DIR / script),
        workdir=str(BASE_DIR),
        icon=icon or str(BASE_DIR / "clock.ico"),
        desc=desc,
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return False, "找不到 powershell"
    out = (r.stdout or "").strip()
    if out.startswith("OK:"):
        return True, out[3:]
    if out == "NO_PYTHONW":
        return False, "找不到 pythonw.exe"
    return False, (r.stderr or out or "未知错误").strip()[:300]


if __name__ == "__main__":
    ok, msg = create_desktop_shortcut()
    print(("✓ " if ok else "✗ ") + msg)
