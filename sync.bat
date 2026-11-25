@echo off
rem Sync data with your other computer via the private GitHub data repo.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python timelog.py sync run
) else (
    py timelog.py sync run
)
echo.
timeout /t 6 >nul
