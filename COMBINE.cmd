@echo off
rem Double-click after copying the other computer's data folder into this data folder.
rem Merges every merged_*.csv and merged_ecg_*.csv of one day into merged_all_<day>.csv
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set /p DAY=Date to combine (YYYYMMDD, e.g. 20260923):
".venv\Scripts\python.exe" combine.py %DAY%
echo.
pause
