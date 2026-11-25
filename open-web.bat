@echo off
rem Open the local dashboard (http://127.0.0.1:8765). Close this window to stop the dashboard.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python timelog.py web
) else (
    py timelog.py web
)
