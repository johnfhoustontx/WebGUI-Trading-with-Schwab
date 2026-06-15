@echo off
title Schwab Web GUI (:8500)
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo ============================================
echo   Schwab Web GUI   http://127.0.0.1:8500
echo ============================================
echo.
echo NOTE: the web gui reads market data through the
echo proxy on :8100. Start it (start_all.bat) if you
echo need live data.
echo.

REM --- spawn a helper that waits for :8500 to bind, then opens the browser ---
start "open browser" /min cmd /c "%~dp0_open_webgui.bat"

echo Starting NiceGUI web app on :8500 ... (keep this window open)
"%PY%" webgui\main.py
