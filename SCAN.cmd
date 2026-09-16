@echo off
rem Double-click to list every Polar sensor nearby (no recording).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\scan.py 10
echo.
pause
