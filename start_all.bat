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
echo   memurai       redis://127.0.0.1:6379  (storage/comm backbone)
echo   proxy         http://127.0.0.1:8100
echo   sentiment_svc http://127.0.0.1:8210
echo   options_svc   http://127.0.0.1:8211  (incl. 5-min GEX history collection)
echo   trade_svc     http://127.0.0.1:8213  (on-demand symbol analysis)
echo   web gui       http://127.0.0.1:8500
echo ============================================
echo.

REM --- 0. Memurai (Redis backbone) must be running for the 3-tier services ---
REM     Memurai installs as a native Windows service on :6379 (start it from services.msc
REM     if this check fails). The sentiment service + GUI use it as the cache/pub-sub/command bus.
echo Checking Memurai (Redis) on :6379...
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',6379);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo   WARNING: Memurai not reachable on :6379. Start the "Memurai" Windows service,
    echo            then re-run. The 3-tier services ^(sentiment_svc, web gui^) need it.
    echo.
) else (
    echo   Memurai is up.
    echo.
)

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

REM --- 2. sentiment service (:8210): owns the 120s sentiment refresh + publishes cache/bridge ---
REM     Tier-2 processing service. Computes the composite via sentiment-dashboard engines and
REM     writes cache:sentiment:* + events to Memurai; the web GUI reads from there.
echo Starting sentiment service in a new window...
start "Sentiment Service (:8210)" cmd /k ""%PY%" services\sentiment_svc\app.py"
echo.

REM --- 3. options service (:8211): owns the 15-min auto-scan + header/gamma/paper/calc/swing ---
REM     Tier-2 processing service. Runs the scanner engines and writes cache:options:* + events
REM     to Memurai (scan, swing, header VIX, gamma, paper account/trades, captured, calculator);
REM     the web GUI reads from there and drives on-demand actions via cmd:options.
echo Starting options service in a new window...
start "Options Service (:8211)" cmd /k ""%PY%" services\options_svc\app.py"
echo.

REM --- 4. GEX history collection now runs INSIDE options_svc (step 3) ---
REM     The options service owns intraday GEX snapshot collection (5-min slots,
REM     08:30-15:20 CT) via its scheduler, so no separate collector window is
REM     started here. options-scanner\gex_collector.py remains a MANUAL fallback
REM     (run it standalone if options_svc is down); its advisory lock makes it
REM     defer to the service when both are up.

REM --- 4b. trade service (:8213): on-demand single-symbol analysis ---
REM     Tier-2 processing service. No scheduler — the web GUI Trade page enqueues
REM     an "analyze" command on cmd:trade and the service computes the MTF
REM     verdicts and writes cache:trade:analysis + event for the page to read.
echo Starting trade service in a new window...
start "Trade Service (:8213)" cmd /k ""%PY%" services\trade_svc\app.py"
echo.

REM --- 5. NiceGUI web app (:8500) ---
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
echo   All services started. Five windows are
echo   running: proxy, sentiment service, options
echo   service (incl. GEX collection), trade
echo   service, web gui. (Memurai runs as a Windows
echo   service.) Close those windows to stop them.
echo ============================================
echo.
echo This launcher window can be closed.
timeout /t 8 /nobreak >nul
