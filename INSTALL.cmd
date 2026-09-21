@echo off
rem One-time setup on a new computer. Double-click. Needs internet.
rem Installs "uv" (Python manager from astral.sh) if missing, then Python 3.11 and all packages into .venv
cd /d "%~dp0"
chcp 65001 >nul
echo ==============================================================
echo  ECG / Polar recorder - one-time installation
echo ==============================================================
where uv >nul 2>nul
if errorlevel 1 (
  if not exist "%USERPROFILE%\.local\bin\uv.exe" (
    echo [1/2] Installing uv ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  )
)
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: uv could not be installed. Check the internet connection and run INSTALL.cmd again.
  pause
  exit /b 1
)
echo [2/2] Installing Python 3.11 and packages ...
set UV_LINK_MODE=copy
uv sync --no-dev
if errorlevel 1 (
  echo.
  echo ERROR: package installation failed. See the messages above.
  pause
  exit /b 1
)
echo.
echo ==============================================================
echo  Done. You can now double-click:
echo    SCAN.cmd              list Polar sensors nearby
echo    START_RECORDING.cmd   record all Polar sensors
echo    START_ECG.cmd         record all TriBLE ECG patches
echo ==============================================================
pause
