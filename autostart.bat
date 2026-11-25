@echo off
rem Toggle start-on-login for the collector.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    set "PY=py"
)
%PY% timelog.py autostart status
echo.
echo [1] Install start-on-login
echo [2] Remove start-on-login
echo [3] Quit
set /p pick=Choose 1/2/3:
if "!pick!"=="1" %PY% timelog.py autostart install
if "!pick!"=="2" %PY% timelog.py autostart remove
endlocal
timeout /t 10 >nul
