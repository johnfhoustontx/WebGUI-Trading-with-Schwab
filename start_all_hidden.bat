@echo off
REM =====================================================================
REM  start_all_hidden.bat
REM
REM  DOUBLE-CLICK this from Windows Explorer (or a desktop shortcut to it)
REM  to launch the whole stack with NO windows:
REM      proxy + the six domain services + the web GUI all run HIDDEN,
REM      with their output redirected to  logs\<name>.out.log / .err.log,
REM      and your browser opens to the web GUI.
REM
REM  It then opens the DEALER-POSITIONING HUD (tools\nq_hud.py) — the one
REM  process here that is meant to be SEEN. It is started last, after the
REM  stack is confirmed up, because it reads Redis and the GEX history the
REM  services populate.
REM
REM  It works by relaunching itself HIDDEN (so not even this console
REM  lingers — you'll see at most a brief flash), then running
REM  start_all_wt.bat in its no-window mode. Memurai (the Redis service)
REM  is left as-is; make sure it's running.
REM
REM  To STOP everything: run stop_all.bat, or open the web GUI and use
REM  More > Terminate (Memurai stays up).
REM  NOTE: stop_all.bat DOES stop the HUD. Everything else it kills is found
REM  by listening port; the HUD binds none, so it is matched on its command
REM  line instead — scoped to THIS checkout's root, so a stop here never
REM  reaches the other environment's HUD. See tools\stop_all.py:_is_hud.
REM =====================================================================

cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

REM Checked BEFORE the guards: with no python their probes fail with errorlevel 1,
REM which would print "this is the DEV checkout" and send you hunting for a marker
REM that is not the problem.
if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.lock
    if /i not "%~1"=="__hidden" pause
    exit /b 1
)

REM =====================================================================
REM  THE GUARDS RUN ON BOTH PASSES, AHEAD OF THE __hidden DISPATCH.
REM
REM  Placement is the whole point here. This file does two things before it
REM  delegates: it relaunches ITSELF hidden, and then it starts the HUD. A
REM  guard sitting in :run would fire only inside the hidden second pass -
REM  its message printed to a console nobody can see - and, worse, the
REM  double-clicked shortcut that triggered the incident would appear to do
REM  nothing at all. Guarding ahead of the dispatch means the refusal lands
REM  in the VISIBLE first-pass console and no second pass, and therefore no
REM  HUD, is ever reached.
REM
REM  `pause` is skipped on the hidden pass: there is no console to read it
REM  and an invisible pause is an invisible hang.
REM =====================================================================

REM --- Refuse to run in a DEV checkout. THIS is the launcher the incident came
REM     through: the desktop shortcut still pointed at the old folder, which is
REM     now dev, so a double-click ran the PROD stack from the dev checkout. It
REM     starts a schwab-proxy, and dev's PROXY_PORT is 8100 - PROD'S, borrowed -
REM     so the proxy bound prod's port while prod itself never started. Nothing
REM     looked wrong; prod was entirely down. No-op in prod (IS_DEV is False).
"%PY%" -c "import sys,repo_paths; sys.exit(1 if repo_paths.IS_DEV else 0)"
if errorlevel 1 (
    echo This is the DEV checkout - refusing to start the PROD stack here.
    echo.
    echo This launcher starts a schwab-proxy, and dev borrows PROD's proxy port
    echo :8100. Run from here it would bind :8100 while prod never starts, so
    echo everything would look healthy while prod was down and a dev-checkout
    echo process served its market data. That is exactly what happened.
    echo.
    echo Use  start_dev.bat  instead. To start PROD, point this shortcut at the
    echo PROD checkout and run it from there.
    if /i not "%~1"=="__hidden" pause
    exit /b 1
)

REM --- Refuse if this environment's stack is already up. Ports come from
REM     tools\check_stack_down.py, which reads them from stop_all's target list
REM     so the starter and the stopper cannot disagree.
"%PY%" tools\check_stack_down.py
if errorlevel 1 (
    if /i not "%~1"=="__hidden" pause
    exit /b 1
)

REM Second pass (relaunched hidden): do the actual work.
if /i "%~1"=="__hidden" goto run

REM First pass (the visible double-click console): relaunch myself hidden,
REM then close this console immediately.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '__hidden' -WindowStyle Hidden"
exit /b

:run
cd /d "%~dp0"
call "%~dp0start_all_wt.bat" nowindow
REM start_all_wt runs the same two guards again. It should not fire - they passed
REM moments ago on this pass - but if it does, the stack was not started and the
REM HUD must not come up polling one that is not there.
if errorlevel 1 exit /b 1
call :launch_hud
exit /b

REM ------------------------------- subroutines -------------------------------

:launch_hud
REM The HUD is deliberately NOT launched through start_all_wt.bat's
REM :launch_hidden helper, for two reasons:
REM   1. That helper passes -WindowStyle Hidden. The HUD is a desktop window
REM      you are meant to look at, so hiding it would defeat the point. Here
REM      pythonw suppresses only the CONSOLE and the GUI still appears.
REM   2. It is not a service. It binds no port, so the web GUI's Status page
REM      cannot health-check it, and stop_all.bat cannot find it the way it
REM      finds everything else — by listening port. stop_all DOES stop it, but
REM      only via a command-line match scoped to this checkout; needing that
REM      exception is exactly why grouping it with the eight port-bound
REM      processes would mislead.
if not exist "%~dp0logs" mkdir "%~dp0logs"
set "HUD_PY=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%HUD_PY%" set "HUD_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%HUD_PY%" goto :eof

REM Skip if one is already up. The services are port-bound, so a second
REM launch of those fails harmlessly; the HUD has no such guard, and two
REM copies would both write the same nq_state.json that the NinjaTrader
REM indicator reads.
REM
REM The Name filter is NOT optional. Matching on CommandLine alone also
REM matches the very PowerShell process running this check — its own command
REM line contains "nq_hud.py" — plus any shell that ever mentioned the script.
REM Measured: 6 matches, of which only 2 were the HUD. Without the filter the
REM guard reports "already running" every single time and the HUD never starts.
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -EA SilentlyContinue | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*nq_hud.py*' }) { Write-Host 'Dealer-Positioning HUD already running - not starting a second one.' } else { Write-Host 'Starting the Dealer-Positioning HUD...'; Start-Process -FilePath '%HUD_PY%' -ArgumentList 'tools\nq_hud.py' -WorkingDirectory '%~dp0' -RedirectStandardOutput '%~dp0logs\nq_hud.out.log' -RedirectStandardError '%~dp0logs\nq_hud.err.log' }"
goto :eof
