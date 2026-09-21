@echo off
rem Double-click to (re)compute heart rate for every ECG recording that has not been processed yet
rem (for example when the recorder window was closed instead of pressing Ctrl+C).
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" ecg_process.py --all
echo.
pause
