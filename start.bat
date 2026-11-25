@echo off
rem Start background time tracking.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python timelog.py start --bg
) else (
    py timelog.py start --bg
)
echo.
echo Tracking started. Run stop.bat to stop it.
timeout /t 6 >nul
