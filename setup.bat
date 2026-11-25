@echo off
rem First-run setup for a new computer.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python setup.py
) else (
    py setup.py
)
echo.
pause
