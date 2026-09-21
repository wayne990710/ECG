@echo off
rem Double-click to record raw ECG from every TriBLE patch nearby.
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
echo  TriBLE ECG patch recorder
echo  1. Switch on every patch. Close the phone app (a patch accepts one connection only).
echo  2. This window scans for 10 s, then records all patches found (max about 9 per computer).
echo  3. Press Ctrl+C here to stop. Heart rate is computed automatically at the end.
echo  Files: %~dp0data
echo ==============================================================
".venv\Scripts\python.exe" trible_logger.py --auto %*
echo.
echo Finished. Files are in: %~dp0data
pause
