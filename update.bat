@echo off
rem Update timelog to the latest version (for machines installed via git clone).
setlocal EnableDelayedExpansion
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py")

echo [1/3] Pulling latest code...
git pull --rebase

echo.
echo [2/3] Syncing data...
%PY% timelog.py sync run

echo.
echo [3/3] Restarting the dashboard...
%PY% timelog.py web --restart

echo.
echo Update complete. The dashboard has been restarted.
timeout /t 8 >nul
