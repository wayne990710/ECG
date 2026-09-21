@echo off
rem Double-click to record heart rate from every Polar Sense nearby.
rem Press Ctrl+C in this window to stop; CSV files are saved in the data folder.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Please double-click INSTALL.cmd first.
  pause
  exit /b 1
)
echo ==============================================================
echo  Polar Verity Sense heart-rate recorder
echo  1. Put every sensor on (blue LED = heart-rate mode).
echo  2. This window scans for 10 s, then records all sensors found.
echo  3. Press Ctrl+C here to stop. CSV files: %~dp0data
echo ==============================================================
".venv\Scripts\python.exe" polar_hr_logger.py --auto %*
echo.
echo Finished. CSV files are in: %~dp0data
pause
