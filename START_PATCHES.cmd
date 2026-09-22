@echo off
rem Double-click on the computer that records the ECG patches. Ctrl+C to stop.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Please double-click INSTALL.cmd first.
  pause
  exit /b 1
)
echo ==============================================================
echo  ECG patches only (two-computer setup)
echo  Devices come from devices.json. Missing ones are searched until they appear.
echo  Press Ctrl+C here to stop. Do NOT close the window with the X button.
echo  Files: %~dp0data
echo ==============================================================
".venv\Scripts\python.exe" record_all.py --only ecg %*
echo.
echo Finished. Files are in: %~dp0data
pause
