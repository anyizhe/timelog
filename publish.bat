@echo off
rem Publish the timelog code to GitHub (public repo).
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python publish.py
) else (
    py publish.py
)
echo.
pause
