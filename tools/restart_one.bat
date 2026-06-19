@echo off
REM restart_one.bat <kill_port|0> <wait_port|0> <script_relpath_from_repo_root>
REM
REM   Frees <kill_port> (taskkills whatever is LISTENING on it, best-effort — so a
REM   wedged process is cleared first), then hands off to wait_and_run.bat to wait
REM   for <wait_port> (0 = skip) and launch the repo venv python on <script>.
REM
REM   Spawned in its own console by the System Status page's per-component Restart
REM   button (and usable standalone). Runs under `cmd /k`, so the window stays open
REM   with the restarted component's live logs.
setlocal
set "HERE=%~dp0"
set "KILLPORT=%~1"
set "WAITPORT=%~2"
set "SCRIPT=%~3"

if not "%KILLPORT%"=="0" (
    echo [restart_one] freeing port %KILLPORT% ^(killing any LISTENING owner^)...
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":%KILLPORT% " ^| findstr /i "LISTENING"') do (
        echo   taskkill PID %%P
        taskkill /F /PID %%P >nul 2>&1
    )
    REM brief pause so the port is fully released before re-binding
    timeout /t 1 /nobreak >nul
)

call "%HERE%wait_and_run.bat" %WAITPORT% %SCRIPT%
