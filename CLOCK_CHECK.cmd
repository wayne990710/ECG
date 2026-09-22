@echo off
rem Double-click to check this computer's clock against internet time.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" clock_check.py
echo.
pause
