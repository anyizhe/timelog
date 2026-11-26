@echo off
rem Update timelog to the latest version (for machines installed via git clone).
setlocal EnableDelayedExpansion
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py")

echo [1/4] Pulling latest code...
git pull --rebase

echo.
echo [2/4] Making sure the collector is running...
%PY% timelog.py start --bg

echo.
echo [3/4] Syncing data...
%PY% timelog.py sync run

echo.
echo [4/4] Restarting the dashboard...
%PY% timelog.py web --restart

echo.
echo Update complete. Current status:
%PY% timelog.py status
timeout /t 10 >nul
