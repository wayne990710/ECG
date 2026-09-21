@echo off
rem Double-click to record TriBLE ECG patches AND Polar sensors together in one window.
rem Press Ctrl+C in this window to stop; files are saved in the data folder.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Please double-click INSTALL.cmd first.
  pause
  exit /b 1
)
echo ==============================================================
echo  ECG patches + Polar sensors recorder  (max 9 devices per computer)
echo  1. Switch on every patch and every Polar (blue LED). Close the phone apps.
echo  2. This window scans for 12 s, then records everything it found.
echo  3. Press Ctrl+C here to stop. Do NOT close the window with the X button.
echo  Files: %~dp0data   (open merged_all_*.csv in Excel)
echo ==============================================================
".venv\Scripts\python.exe" record_all.py %*
echo.
echo Finished. Files are in: %~dp0data
pause
