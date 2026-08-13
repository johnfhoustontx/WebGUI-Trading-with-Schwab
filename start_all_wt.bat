@echo off
title NeuralStrike - start all (Windows Terminal, one window)
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM --- Mode select. Default = ONE Windows Terminal window with 8 live-log tabs.
REM     Pass  nowindow  (aliases: -nowindow / /nowindow / hidden) to launch every
REM     process with NO WINDOW at all — each runs hidden and its output is
REM     redirected to logs\<name>.out.log / .err.log. Stop a windowless stack with
REM     stop_all.bat or the web GUI's More > Terminate page (Memurai stays up).
REM     Only the FLAG is read here; the branch is taken after the guards below,
REM     which must cover the windowless mode too. Knowing the mode early is what
REM     lets a refusal skip `pause`: a windowless launch may have no console to
REM     read it, and an invisible pause is an invisible hang.
set "HIDDEN="
if /i "%~1"=="nowindow"  set "HIDDEN=1"
if /i "%~1"=="-nowindow" set "HIDDEN=1"
if /i "%~1"=="/nowindow" set "HIDDEN=1"
if /i "%~1"=="hidden"    set "HIDDEN=1"

REM --- Refuse to run in a DEV checkout. The mirror of start_dev.bat's guard, and
REM     the one that actually caused harm: a desktop shortcut still pointing at
REM     the old folder - now dev - ran a PROD launcher from the dev checkout.
REM     This launcher starts a schwab-proxy, and dev's PROXY_PORT is 8100, which
REM     is PROD'S port, borrowed. So the proxy bound :8100 from a dev checkout
REM     while prod itself never started: the stack LOOKED up, and prod was
REM     entirely down. No-op in prod, where IS_DEV is False.
REM     `if errorlevel 1` is "1 or greater", so a python crash refuses too.
"%PY%" -c "import sys,repo_paths; sys.exit(1 if repo_paths.IS_DEV else 0)"
if errorlevel 1 (
    echo This is the DEV checkout - refusing to start the PROD stack here.
    echo.
    echo This launcher starts a schwab-proxy, and dev borrows PROD's proxy port
    echo :8100. Run from here it would bind :8100 while prod never starts, so
    echo everything would look healthy while prod was down and a dev-checkout
    echo process served its market data.
    echo.
    echo Use  start_dev.bat  instead. To start PROD, run its launcher from the
    echo PROD checkout.
    if not defined HIDDEN pause
    exit /b 1
)

REM --- Refuse if this environment's stack is already up. Ports come from
REM     tools\check_stack_down.py, which reads them from stop_all's target list
REM     so the starter and the stopper cannot disagree. Starting twice spawns
REM     duplicates that each do a full startup - real Schwab API calls - before
REM     failing to bind and exiting.
"%PY%" tools\check_stack_down.py
if errorlevel 1 (
    if not defined HIDDEN pause
    exit /b 1
)

if defined HIDDEN goto hidden

REM =========================================================================
REM  DEFAULT MODE — one Windows Terminal window, 8 tabs, live logs
REM =========================================================================
where wt >nul 2>&1
if errorlevel 1 (
    echo Windows Terminal ^(wt.exe^) was not found on PATH.
    echo Install it from the Microsoft Store, or:  winget install Microsoft.WindowsTerminal
    echo Or use start_all.bat ^(separate windows^), or  start_all_wt.bat nowindow  ^(no windows^).
    pause
    exit /b 1
)

echo ============================================
echo   NeuralStrike - launching in ONE Windows
echo   Terminal window with 8 tabs (live logs):
echo     Proxy :8100  . Sentiment :8210 . Options :8211
echo     Portfolio :8212 . Trade :8213 . Driver :8214
echo     Market :8215 . Web GUI :8500  (Memurai runs as a Windows service)
echo   (Tip: run  start_all_wt.bat nowindow  to launch with no windows.)
echo ============================================
echo.

call :memurai_check

REM --- Launch all 8 processes as tabs in a SINGLE Windows Terminal window. ---
REM     The proxy tab starts immediately (wait port 0); every other tab waits for
REM     the proxy on :8100 first (tools\wait_and_run.bat) so services don't spam
REM     errors during proxy startup. Each tab runs under `cmd /k` so it stays open
REM     with live logs. `-d "%CD%"` starts each tab in the repo root; the `;`
REM     between new-tab clauses tells wt to put every tab in the same window.
echo Opening Windows Terminal with all services...
wt new-tab -d "%CD%" --title "Proxy :8100" cmd /k call tools\wait_and_run.bat 0 schwab-proxy\schwab_proxy.py ; new-tab -d "%CD%" --title "Sentiment :8210" cmd /k call tools\wait_and_run.bat 8100 services\sentiment_svc\app.py ; new-tab -d "%CD%" --title "Options :8211" cmd /k call tools\wait_and_run.bat 8100 services\options_svc\app.py ; new-tab -d "%CD%" --title "Portfolio :8212" cmd /k call tools\wait_and_run.bat 8100 services\portfolio_svc\app.py ; new-tab -d "%CD%" --title "Trade :8213" cmd /k call tools\wait_and_run.bat 8100 services\trade_svc\app.py ; new-tab -d "%CD%" --title "Driver :8214" cmd /k call tools\wait_and_run.bat 8100 services\driver_svc\app.py ; new-tab -d "%CD%" --title "Market :8215" cmd /k call tools\wait_and_run.bat 8100 services\market_svc\app.py ; new-tab -d "%CD%" --title "Web GUI :8500" cmd /k call tools\wait_and_run.bat 8100 webgui\main.py

call :wait_web
call :open_browser

echo.
echo ============================================
echo   All 8 processes launched in ONE Windows
echo   Terminal window (8 tabs). Close that window
echo   (or each tab) to stop the services.
echo   This launcher window can be closed.
echo ============================================
timeout /t 6 /nobreak >nul
goto :eof

REM =========================================================================
REM  NO-WINDOW MODE — every process hidden, output redirected to logs\
REM =========================================================================
:hidden
echo ============================================
echo   NeuralStrike - launching with NO WINDOWS.
echo   All 8 processes run hidden; output goes to
echo     %~dp0logs\^<name^>.out.log  (and .err.log)
echo   Stop them with stop_all.bat, or the web GUI
echo   More ^> Terminate page (Memurai stays up).
echo ============================================
echo.
if not exist "%~dp0logs" mkdir "%~dp0logs"

call :memurai_check

echo Starting proxy (hidden) and waiting for :8100...
call :launch_hidden proxy schwab-proxy\schwab_proxy.py
:waitproxy_h
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8100);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitproxy_h
echo Proxy is up.
echo.

echo Starting the six services + web GUI (hidden)...
call :launch_hidden sentiment_svc services\sentiment_svc\app.py
call :launch_hidden options_svc   services\options_svc\app.py
call :launch_hidden portfolio_svc services\portfolio_svc\app.py
call :launch_hidden trade_svc     services\trade_svc\app.py
call :launch_hidden driver_svc    services\driver_svc\app.py
call :launch_hidden market_svc    services\market_svc\app.py
call :launch_hidden webgui        webgui\main.py

call :wait_web
call :open_browser

echo.
echo ============================================
echo   All 8 processes are running hidden. Tail
echo   logs in  %~dp0logs\  or watch the web GUI's
echo   More ^> System Status page. Stop everything
echo   with stop_all.bat (Memurai stays up).
echo ============================================
timeout /t 6 /nobreak >nul
goto :eof

REM ------------------------------- subroutines -------------------------------

:memurai_check
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
goto :eof

:launch_hidden
REM %1 = log-name, %2 = script (relative to repo root). Runs hidden via pythonw
REM (falls back to python -WindowStyle Hidden) with stdout/stderr -> logs\.
set "H_PY=%PYW%"
if not exist "%PYW%" set "H_PY=%PY%"
echo   - %~2  ^(logs\%~1.out.log^)
powershell -NoProfile -Command "Start-Process -FilePath '%H_PY%' -ArgumentList '%~2' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0logs\%~1.out.log' -RedirectStandardError '%~dp0logs\%~1.err.log'"
goto :eof

:wait_web
echo Waiting for web gui to bind :8500...
:waitweb
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8500);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitweb
echo Web gui is up.
echo.
goto :eof

:open_browser
echo Opening http://127.0.0.1:8500 in your browser...
start "" "http://127.0.0.1:8500"
goto :eof
