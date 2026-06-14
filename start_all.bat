@echo off
title Schwab Trading - start all
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo ============================================
echo   Schwab Trading - launching services
echo   proxy   http://127.0.0.1:8100
echo   web gui http://127.0.0.1:8500
echo ============================================
echo.
echo Starting schwab-proxy in a new window (keep it open)...
start "Schwab API Proxy (:8100)" cmd /k ""%PY%" schwab-proxy\schwab_proxy.py"

echo Waiting for proxy to bind :8100...
:waitproxy
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8100);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitproxy

echo Proxy is up. Starting NiceGUI app on :8500 ...
echo (Open http://127.0.0.1:8500 in your browser.)
"%PY%" webgui\main.py
