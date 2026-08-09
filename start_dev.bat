@echo off
title NeuralStrike DEV - start dev stack (Windows Terminal, one window)
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"

REM =====================================================================
REM  start_dev.bat — the DEV stack.
REM
REM  A SEPARATE file rather than a flag on start_all_wt.bat, deliberately:
REM  prod's launcher has to come up every morning and its behavior is not
REM  changing. The two differences from start_all_wt.bat are that this one
REM  starts SEVEN processes instead of eight (there is no dev proxy — see
REM  below) and that it refuses to run outside a dev-marked checkout.
REM
REM  Ports come from config\ports.toml plus the [dev] profile's port_offset
REM  in config\environments.toml. The numbers below are only labels — every
REM  process reads its own port from repo_paths — but they must still track
REM  that table; tests\test_launcher_ports.py pins them.
REM
REM  Design: docs\plans\2026-08-08-dev-prod-environments-design.md
REM =====================================================================

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.lock
    pause
    exit /b 1
)

REM --- Refuse to run unless this checkout is marked dev. Launching the dev stack
REM     from the prod checkout would try to bind prod's ports a second time and
REM     fail confusingly — seven processes dying on "address already in use"
REM     while the real stack keeps serving. Checked BEFORE the mode select so it
REM     applies to the windowless mode too, where those failures are invisible.
"%PY%" -c "import sys,repo_paths; sys.exit(0 if repo_paths.IS_DEV else 1)"
if errorlevel 1 (
    echo This checkout is not marked as dev.
    echo Create config\env.local.toml containing:  name = "dev"
    echo ^(see config\env.local.example.toml^)
    pause
    exit /b 1
)

REM --- Mode select. Default = ONE Windows Terminal window with 7 live-log tabs.
REM     Pass  nowindow  (aliases: -nowindow / /nowindow / hidden) to launch every
REM     process with NO WINDOW at all — each runs hidden and its output is
REM     redirected to logs\<name>.out.log / .err.log. Stop a windowless stack with
REM     stop_all.bat or the web GUI's More > Terminate page (Memurai stays up,
REM     and so does prod's proxy — this environment does not own it).
set "HIDDEN="
if /i "%~1"=="nowindow"  set "HIDDEN=1"
if /i "%~1"=="-nowindow" set "HIDDEN=1"
if /i "%~1"=="/nowindow" set "HIDDEN=1"
if /i "%~1"=="hidden"    set "HIDDEN=1"
if defined HIDDEN goto hidden

REM =========================================================================
REM  DEFAULT MODE — one Windows Terminal window, 7 tabs, live logs
REM =========================================================================
where wt >nul 2>&1
if errorlevel 1 (
    echo Windows Terminal ^(wt.exe^) was not found on PATH.
    echo Install it from the Microsoft Store, or:  winget install Microsoft.WindowsTerminal
    echo Or use  start_dev.bat nowindow  ^(no windows^).
    pause
    exit /b 1
)

echo ============================================
echo   NeuralStrike DEV - launching in ONE Windows
echo   Terminal window with 7 tabs (live logs):
echo     Sentiment :9210 . Options :9211 . Portfolio :9212
echo     Trade :9213 . Driver :9214 . Market :9215
echo     Web GUI :9500   (Memurai runs as a Windows service, DB 1)
echo.
echo   NO PROXY TAB - dev borrows the PROD checkout's proxy on :8100,
echo   because there is one rotating Schwab OAuth refresh token and a
echo   second proxy cannot safely hold it. Start prod first.
echo   (Tip: run  start_dev.bat nowindow  to launch with no windows.)
echo ============================================
echo.

call :memurai_check
REM Check PROD's proxy BEFORE opening seven tabs that would all sit blocked on it.
REM The hidden branch already did this; the tab branch did not, on the theory that a
REM visible "waiting for dependency on :8100" tab was self-explanatory. It is not:
REM the tabs are in a SEPARATE Windows Terminal window, while this window sits on a
REM message about the web GUI — so starting dev before prod looked like "the dev
REM launcher is broken". Observed, not hypothesised.
call :wait_prod_proxy

REM --- Launch all 7 processes as tabs in a SINGLE Windows Terminal window. ---
REM     Every tab waits for :8100 first (tools\wait_and_run.bat). That is PROD'S
REM     proxy, and waiting on it is the point: dev's on-demand fetches go through
REM     it, so a visible "waiting for dependency on 127.0.0.1:8100" tab is a clear,
REM     early report that prod is down — far better than seven services quietly
REM     failing every fetch. Each tab runs under `cmd /k` so it stays open with
REM     live logs. `-d "%CD%"` starts each tab in the repo root; the `;` between
REM     new-tab clauses tells wt to put every tab in the same window.
echo Opening Windows Terminal with the dev services...
wt new-tab -d "%CD%" --title "DEV Sentiment :9210" cmd /k call tools\wait_and_run.bat 8100 services\sentiment_svc\app.py ; new-tab -d "%CD%" --title "DEV Options :9211" cmd /k call tools\wait_and_run.bat 8100 services\options_svc\app.py ; new-tab -d "%CD%" --title "DEV Portfolio :9212" cmd /k call tools\wait_and_run.bat 8100 services\portfolio_svc\app.py ; new-tab -d "%CD%" --title "DEV Trade :9213" cmd /k call tools\wait_and_run.bat 8100 services\trade_svc\app.py ; new-tab -d "%CD%" --title "DEV Driver :9214" cmd /k call tools\wait_and_run.bat 8100 services\driver_svc\app.py ; new-tab -d "%CD%" --title "DEV Market :9215" cmd /k call tools\wait_and_run.bat 8100 services\market_svc\app.py ; new-tab -d "%CD%" --title "DEV Web GUI :9500" cmd /k call tools\wait_and_run.bat 8100 webgui\main.py

call :wait_web
if errorlevel 1 (call :web_timeout_help) else (call :open_browser)

echo.
echo ============================================
echo   All 7 dev processes launched in ONE Windows
echo   Terminal window (7 tabs). Close that window
echo   (or each tab) to stop them.
echo   This launcher window can be closed.
echo ============================================
timeout /t 6 /nobreak >nul
goto :eof

REM =========================================================================
REM  NO-WINDOW MODE — every process hidden, output redirected to logs\
REM =========================================================================
:hidden
echo ============================================
echo   NeuralStrike DEV - launching with NO WINDOWS.
echo   All 7 processes run hidden; output goes to
echo     %~dp0logs\^<name^>.out.log  (and .err.log)
echo   No proxy is started - dev borrows prod's on :8100.
echo   Stop them with stop_all.bat, or the web GUI
echo   More ^> Terminate page (Memurai stays up).
echo ============================================
echo.
if not exist "%~dp0logs" mkdir "%~dp0logs"

call :memurai_check
call :wait_prod_proxy

echo Starting the six dev services + web GUI (hidden)...
call :launch_hidden sentiment_svc services\sentiment_svc\app.py
call :launch_hidden options_svc   services\options_svc\app.py
call :launch_hidden portfolio_svc services\portfolio_svc\app.py
call :launch_hidden trade_svc     services\trade_svc\app.py
call :launch_hidden driver_svc    services\driver_svc\app.py
call :launch_hidden market_svc    services\market_svc\app.py
call :launch_hidden webgui        webgui\main.py

call :wait_web
if errorlevel 1 (call :web_timeout_help) else (call :open_browser)

echo.
echo ============================================
echo   All 7 dev processes are running hidden. Tail
echo   logs in  %~dp0logs\  or watch the web GUI's
echo   More ^> System Status page. Stop everything
echo   with stop_all.bat (Memurai and prod's proxy
echo   stay up - this environment owns neither).
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
    echo   Memurai is up ^(dev uses logical DB 1^).
    echo.
)
goto :eof

:wait_prod_proxy
REM The windowless counterpart of the tabs' wait_and_run 8100. BOUNDED, unlike
REM that one: wait_and_run loops forever, which is fine in a tab you can read but
REM would be an invisible hang here. Dev's services start fine without the proxy —
REM they only need it for on-demand fetches — so after 60 attempts say so and
REM carry on. Counted in ATTEMPTS, not seconds: each probe spawns a PowerShell,
REM which measured ~2.5s on its own, so "60 tries" is several minutes and a
REM message promising "60s" would just be wrong.
echo Waiting for the PROD proxy on :8100 (up to 60 attempts)...
set /a PROXY_TRIES=0
:waitproxy_dev
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8100);exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    echo   Prod proxy is up.
    echo.
    goto :eof
)
set /a PROXY_TRIES+=1
if %PROXY_TRIES% GEQ 60 (
    echo   WARNING: no proxy on :8100 after 60 attempts. Starting dev anyway;
    echo            it will run, but every on-demand fetch will fail until the
    echo            PROD checkout's proxy is up.
    echo.
    goto :eof
)
timeout /t 1 /nobreak >nul
goto waitproxy_dev

:launch_hidden
REM %1 = log-name, %2 = script (relative to repo root). Runs hidden via pythonw
REM (falls back to python -WindowStyle Hidden) with stdout/stderr -> logs\.
set "H_PY=%PYW%"
if not exist "%PYW%" set "H_PY=%PY%"
echo   - %~2  ^(logs\%~1.out.log^)
powershell -NoProfile -Command "Start-Process -FilePath '%H_PY%' -ArgumentList '%~2' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0logs\%~1.out.log' -RedirectStandardError '%~dp0logs\%~1.err.log'"
goto :eof

:wait_web
REM BOUNDED, and it must be. Unbounded, this loop is where "start dev before prod"
REM goes to die: every tab blocks on :8100, nothing ever binds :9500, and this
REM window spins forever on a message naming the wrong component. Returns
REM errorlevel 1 on timeout so the caller can explain instead of opening a dead
REM page. Counted in ATTEMPTS like :wait_prod_proxy — each probe spawns a
REM PowerShell, so this is minutes, not 60 seconds.
echo Waiting for the dev web gui to bind :9500...
set /a WEB_TRIES=0
:waitweb
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',9500);exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
    echo Dev web gui is up.
    echo.
    exit /b 0
)
set /a WEB_TRIES+=1
if %WEB_TRIES% LSS 60 goto waitweb
exit /b 1

:web_timeout_help
echo.
echo   The dev web GUI never bound :9500.
echo.
echo   Most likely cause: the PROD checkout's proxy on :8100 is not running.
echo   Every dev tab waits for that proxy before it starts, so nothing binds
echo   :9500 and there is no page to open. Start prod first:
echo       D:\WebGUI Trading Prod\start_all_wt.bat
echo.
echo   The dev tabs are still open and will start on their own once the proxy
echo   appears - you do not need to re-run this. Then browse to:
echo       http://127.0.0.1:9500
echo.
goto :eof

:open_browser
echo Opening http://127.0.0.1:9500 in your browser...
start "" "http://127.0.0.1:9500"
goto :eof
