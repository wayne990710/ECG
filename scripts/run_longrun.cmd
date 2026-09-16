@echo off
rem Run a long recording as an independent process (started by schtasks).
rem Usage: scripts\run_longrun.cmd <seconds> <deviceId...>
rem ASCII only: cmd.exe mis-parses UTF-8 comments under a non-UTF-8 code page.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set DUR=%1
shift
set DEVS=
:loop
if "%~1"=="" goto run
set DEVS=%DEVS% %1
shift
goto loop
:run
for /f "usebackq" %%t in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set STAMP=%%t
set LOG=data\longrun_%STAMP%.log
".venv\Scripts\python.exe" polar_hr_logger.py --duration %DUR% --status-interval 300 --out data %DEVS% > "%LOG%" 2>&1
