@echo off
rem Generate today's daily report + this week's weekly report, then open the reports folder.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python timelog.py report daily
    python timelog.py report weekly
    python timelog.py open
) else (
    py timelog.py report daily
    py timelog.py report weekly
    py timelog.py open
)
timeout /t 8 >nul
