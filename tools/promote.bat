@echo off
REM =====================================================================
REM  tools\promote.bat
REM
REM  Promote main into THIS checkout and restart the stack. Prod-only, on
REM  purpose: prod is pinned to main and changes only when you say so.
REM  Dev promotes by merging to main and pushing, not by running this.
REM
REM  Design: docs\plans\2026-08-08-dev-prod-environments-design.md
REM =====================================================================
cd /d "%~dp0.."
set "PY=%CD%\.venv\Scripts\python.exe"

REM Checked BEFORE the environment guard: with no python the guard's probe fails
REM with errorlevel 1, which would print "this checkout is marked dev" and send
REM you hunting for a marker that is not the problem.
if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    pause
    exit /b 1
)

REM --- Refuse in a dev checkout. The INVERSE of start_dev.bat's guard: that one
REM     exits 0 in dev, this one exits 0 in prod. Both branch on `if errorlevel 1`
REM     ("1 or greater"), so a python crash refuses too, rather than restarting a
REM     stack off a branch nobody chose.
"%PY%" -c "import sys,repo_paths; sys.exit(1 if repo_paths.IS_DEV else 0)"
if errorlevel 1 (
    echo This checkout is marked dev. Promote runs in the PROD checkout.
    pause
    exit /b 1
)

REM --- Refuse on a dirty tree, and refuse BEFORE stopping anything.
REM     `git checkout main` fails loudly only on a CONFLICTING local change; a
REM     non-conflicting one is carried along silently, leaving prod running
REM     something that is not main. That defeats the premise this whole design
REM     rests on - prod is pinned to main and changes only when you say so.
REM     Refusing rather than stashing or cleaning is the point: an unexpected
REM     edit in the prod checkout is for a human to look at, not for a restart
REM     script to decide about. Ordered ahead of stop_all.bat so a refusal never
REM     leaves the stack down.
REM     Ignored paths (the venv, logs\, data\, the gitignored secrets and
REM     env.local.toml) do not appear in --porcelain, so this stays quiet about
REM     everything prod legitimately carries.
set "DIRTY="
for /f "delims=" %%i in ('git status --porcelain') do set "DIRTY=1"
if defined DIRTY (
    echo This checkout has local changes - refusing to promote.
    echo Prod is pinned to main; commit, revert or move these aside first:
    echo.
    git status --short
    echo.
    pause
    exit /b 1
)

echo Stopping the stack...
REM --- Both launchers are called by QUALIFIED path, never a bare name. cmd will
REM     not resolve a bare .bat against the current directory when
REM     NoDefaultCurrentDirectoryInExePath is set in the caller's environment —
REM     the same variable already blamed in the restart-evidence note below. On
REM     2026-08-19 it bit here instead: both calls died with "is not recognized
REM     as an internal or external command", so the stop never ran, the pull
REM     SUCCEEDED, and the restart never ran — prod left on the old build with
REM     its proxy down. `cd /d "%~dp0.."` above makes the repo root current, but
REM     current is exactly what cmd refuses to search.
call "%~dp0..\stop_all.bat"

REM --- Wait for the ports to actually free before relaunching.
REM     stop_all is best-effort and taskkill only REQUESTS termination, so
REM     "stop_all returned" is not "the ports are free". Without this wait the
REM     restart can fail SILENTLY: start_all_wt.bat's proxy wait asks only
REM     "does :8100 accept a connection", which a dying proxy still satisfies —
REM     so the six services launch against a proxy about to vanish while the NEW
REM     proxy exits with a bind error into a log nobody is watching, leaving prod
REM     with no market data. The same applies to the web GUI's port.
REM     Ports come from repo_paths (config\ports.toml), never a literal. The
REM     RELATIVE interpreter path is deliberate: %PY% is absolute and this repo
REM     lives under a path containing spaces, which `for /f` mis-splits.
set "P_PROXY="
set "P_WEB="
for /f "tokens=1,2" %%a in ('.venv\Scripts\python.exe -c "import repo_paths as r;print(r.PROXY_PORT, r.NICEGUI_PORT)"') do (
    set "P_PROXY=%%a"
    set "P_WEB=%%b"
)
if not defined P_PROXY goto :no_ports
call :wait_port_free %P_PROXY% proxy
call :wait_port_free %P_WEB% "web GUI"
goto :ports_free

:no_ports
echo   Could not read the ports from repo_paths; pausing 5s instead.
timeout /t 5 /nobreak >nul

:ports_free
echo Pulling main...
git checkout main || (echo could not switch to main & pause & exit /b 1)

REM --- The pre-pull commit, CAPTURED rather than read back afterwards as
REM     HEAD@{1}. That reflog reference is wrong in both of the cases prod
REM     actually hits, and both were measured:
REM       * a FRESH CLONE - which is exactly what the prod checkout is - has a
REM         single reflog entry, so HEAD@{1} exits 128 and the very first
REM         promote always reinstalls;
REM       * when the pull brings nothing new HEAD does not move, so HEAD@{1} is
REM         whatever preceded it - here the commit `git checkout main` moved off
REM         - and the diff reports a lockfile change the pull never made.
set "BEFORE="
for /f "delims=" %%i in ('git rev-parse HEAD') do set "BEFORE=%%i"

git pull --ff-only || (echo pull failed - resolve manually & pause & exit /b 1)

REM --- Reinstall only when the lockfile actually moved. Written flat rather than
REM     as a nested if/else block on purpose: %BEFORE% inside a parenthesised
REM     block expands when the block is PARSED, which is the classic way a batch
REM     conditional silently tests the wrong value.
REM     An unreadable BEFORE, or a git error, reinstalls - a wasted minute is the
REM     cheaper of the two mistakes.
if not defined BEFORE goto :reinstall
git diff --quiet %BEFORE% HEAD -- requirements.lock
if errorlevel 1 goto :reinstall
echo requirements.lock unchanged - skipping the reinstall.
goto :restart

:reinstall
echo requirements.lock changed - reinstalling...
"%PY%" -m pip install -r requirements.lock

:restart
echo Restarting...
call "%~dp0..\start_all_wt.bat" nowindow

REM --- Judge the restart on EVIDENCE - whether the stack ANSWERS - and not on the
REM     launcher's errorlevel. That errorlevel was the test here until 2026-08-19,
REM     and it was wrong in BOTH directions on consecutive promotes:
REM
REM       * the launcher never ran at all (cmd could not resolve a bare .bat name
REM         because NoDefaultCurrentDirectoryInExePath was set in the caller's
REM         environment) and promote said nothing alarming while prod sat stopped;
REM       * the launcher started all eight processes and THEN returned non-zero,
REM         because a timeout/pause inside it hit "Input redirection is not
REM         supported" under a non-interactive stdin - and promote announced
REM         "Prod is now STOPPED" over a completely healthy stack.
REM
REM     A batch file's exit code reports whether its last console operation
REM     succeeded, which is not the question being asked. check_stack_up.py asks
REM     the stack: an HTTP probe per port, retried to a deadline, port set taken
REM     from stop_all._targets() so the starter, the stopper and the verifier
REM     cannot disagree about what this environment owns. HTTP rather than a TCP
REM     connect on purpose - a process whose accept loop has died stays bound and
REM     still completes a handshake, which has hidden a dead UI here before.
"%PY%" "%CD%\tools\check_stack_up.py"
if errorlevel 1 (
    echo.
    echo The restart did not come up - see the ports listed above.
    pause
    exit /b 1
)
echo Promoted. Check /status.
goto :eof

REM ------------------------------- subroutines -------------------------------

:wait_port_free
REM %1 = port, %2 = label. Waits up to 30 ATTEMPTS for nothing to be LISTENING
REM there, then warns and carries on - a hard stop here would leave prod down,
REM which is worse than a restart that may need a second look.
REM Attempts, not seconds: each probe spawns a PowerShell, which measured ~2.5s
REM on its own, so a message promising "30s" would simply be wrong.
REM DELIBERATELY a TCP connect, not the HTTP probe the start-up waits now use:
REM this waits for a socket to DISAPPEAR. An HTTP GET failing here is ambiguous
REM -- gone, or bound but broken -- so it would report a still-held port as free
REM and let the relaunch race the process it is waiting for.
set /a FREE_TRIES=0
:waitfree
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',%~1);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto :eof
set /a FREE_TRIES+=1
if %FREE_TRIES% GEQ 30 (
    echo   WARNING: still listening on :%~1 ^(%~2^) after 30 attempts.
    echo            Restarting anyway - check logs\ if the stack comes up wrong.
    goto :eof
)
timeout /t 1 /nobreak >nul
goto waitfree
