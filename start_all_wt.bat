@echo off
title Schwab Trading - start all (Windows Terminal, one window)
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM --- Require Windows Terminal (wt.exe). ---
where wt >nul 2>&1
if errorlevel 1 (
    echo Windows Terminal ^(wt.exe^) was not found on PATH.
    echo Install it from the Microsoft Store, or:  winget install Microsoft.WindowsTerminal
    echo Or use start_all.bat ^(separate windows^) instead.
    pause
    exit /b 1
)

echo ============================================
echo   Schwab Trading - launching in ONE Windows
echo   Terminal window with 8 tabs (live logs):
echo     Proxy :8100  . Sentiment :8210 . Options :8211
echo     Portfolio :8212 . Trade :8213 . Driver :8214
echo     Market :8215 . Web GUI :8500  (Memurai runs as a Windows service)
echo ============================================
echo.

REM --- 0. Memurai (Redis backbone) must be running for the 3-tier services. ---
echo Checking Memurai (Redis) on :6379...
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',6379);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo   WARNING: Memurai not reachable on :6379. Start the "Memurai" Windows service,
    echo            then re-run. The 3-tier services and the web GUI need it.
    echo.
) else (
    echo   Memurai is up.
    echo.
)

REM --- Launch all 8 processes as tabs in a SINGLE Windows Terminal window. ---
REM     The proxy tab starts immediately (wait port 0); every other tab waits for
REM     the proxy on :8100 first (tools\wait_and_run.bat) so services don't spam
REM     errors during proxy startup — same ordering the old multi-window launcher
REM     enforced. Each tab runs under `cmd /k` so it stays open with live logs and
REM     survives its service exiting. `-d "%CD%"` starts each tab in the repo root
REM     so the relative wait_and_run.bat + script paths resolve. The `;` between
REM     new-tab clauses tells wt to put every tab in the same window.
echo Opening Windows Terminal with all services...
wt new-tab -d "%CD%" --title "Proxy :8100" cmd /k call tools\wait_and_run.bat 0 schwab-proxy\schwab_proxy.py ; new-tab -d "%CD%" --title "Sentiment :8210" cmd /k call tools\wait_and_run.bat 8100 services\sentiment_svc\app.py ; new-tab -d "%CD%" --title "Options :8211" cmd /k call tools\wait_and_run.bat 8100 services\options_svc\app.py ; new-tab -d "%CD%" --title "Portfolio :8212" cmd /k call tools\wait_and_run.bat 8100 services\portfolio_svc\app.py ; new-tab -d "%CD%" --title "Trade :8213" cmd /k call tools\wait_and_run.bat 8100 services\trade_svc\app.py ; new-tab -d "%CD%" --title "Driver :8214" cmd /k call tools\wait_and_run.bat 8100 services\driver_svc\app.py ; new-tab -d "%CD%" --title "Market :8215" cmd /k call tools\wait_and_run.bat 8100 services\market_svc\app.py ; new-tab -d "%CD%" --title "Web GUI :8500" cmd /k call tools\wait_and_run.bat 8100 webgui\main.py

echo Waiting for web gui to bind :8500...
:waitweb
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8500);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitweb
echo Web gui is up.
echo.

echo Opening http://127.0.0.1:8500 in your browser...
start "" "http://127.0.0.1:8500"

echo.
echo ============================================
echo   All 8 processes launched in ONE Windows
echo   Terminal window (8 tabs). Close that window
echo   (or each tab) to stop the services.
echo   This launcher window can be closed.
echo ============================================
timeout /t 6 /nobreak >nul
