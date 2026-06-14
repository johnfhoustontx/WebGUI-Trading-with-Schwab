@echo off
:: ─────────────────────────────────────────────────────
::  GEX / Charm Background Data Collector
::  Polls Schwab every 5 min (08:30–15:20 CT, Mon–Fri)
::  Writes snapshots to gex_history.db
:: ─────────────────────────────────────────────────────
::  DEPRECATED AS AUTOMATION — MANUAL FALLBACK ONLY.
::  The GEX collector now auto-starts INSIDE the gamma tool
::  (run dashboard.py, then open the gamma window). You no
::  longer need Windows Task Scheduler for this.
::  This script remains as a manual fallback. It will STAND
::  DOWN (defer) if the gamma tool's in-tool collector already
::  owns data/gex_collector.lock — only one collector writes
::  at a time.
:: ─────────────────────────────────────────────────────

set PROJECT_DIR=%~dp0
set PYTHON_EXE=C:\Users\john_\AppData\Local\Programs\Python\Python311\python.exe

echo ================================================
echo   GEX Collector - MANUAL FALLBACK
echo   NOTE: the collector now auto-starts inside the
echo   gamma tool (dashboard.py - gamma window). This
echo   script is a manual fallback only and will defer
echo   if the gamma tool already owns
echo   data\gex_collector.lock.
echo ================================================
echo.

echo ================================================
echo   GEX Collector - Starting...
echo   Project : %PROJECT_DIR%
echo   Python  : %PYTHON_EXE%
echo   Time    : %date% %time%
echo ================================================
echo.

cd /d "%PROJECT_DIR%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python not found at %PYTHON_EXE%
    echo Update PYTHON_EXE in this batch file.
    timeout /t 30 /nobreak >nul
    exit /b 1
)

if not exist "gex_collector.py" (
    echo ERROR: gex_collector.py not found in %PROJECT_DIR%
    timeout /t 30 /nobreak >nul
    exit /b 1
)

echo Starting collector in background... (close Collector window to stop)
echo Log file: %PROJECT_DIR%\logs\gex_collector.log
echo.

start "GEX Collector" cmd /k ""%PYTHON_EXE%" gex_collector.py"
