@echo off
rem Stop background time tracking (also writes today's daily report).
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python timelog.py stop
) else (
    py timelog.py stop
)
echo.
timeout /t 5 >nul
