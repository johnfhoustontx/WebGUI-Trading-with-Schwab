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
echo   proxy         http://127.0.0.1:8100
echo   web gui       http://127.0.0.1:8500
echo   gex collector (5-min snapshots + bridge)
echo ============================================
echo.

REM --- 1. schwab-proxy (must be up first; everything else reads market data through it) ---
echo Starting schwab-proxy in a new window (keep it open)...
start "Schwab API Proxy (:8100)" cmd /k ""%PY%" schwab-proxy\schwab_proxy.py"

echo Waiting for proxy to bind :8100...
:waitproxy
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8100);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitproxy
echo Proxy is up.
echo.

REM --- 2. GEX collector (options-scanner): 5-min GEX snapshots + sentiment-bridge publish ---
REM     Stands down if the gamma tool already owns data\gex_collector.lock; exits past ~15:20 CT.
echo Starting GEX collector in a new window...
start "GEX Collector" cmd /k "cd /d "%~dp0options-scanner" ^&^& "%PY%" gex_collector.py"
echo.

REM --- 3. NiceGUI web app (:8500) ---
echo Starting NiceGUI web app on :8500 in a new window...
start "Schwab Web GUI (:8500)" cmd /k ""%PY%" webgui\main.py"

echo Waiting for web gui to bind :8500...
:waitweb
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8500);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitweb
echo Web gui is up.
echo.

REM --- 4. open the browser to the web gui ---
echo Opening http://127.0.0.1:8500 in your browser...
start "" "http://127.0.0.1:8500"

echo.
echo ============================================
echo   All services started. Three windows are
echo   running: proxy, GEX collector, web gui.
echo   Close those windows to stop the services.
echo ============================================
echo.
echo This launcher window can be closed.
timeout /t 8 /nobreak >nul
