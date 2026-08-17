@echo off
REM wait_and_run.bat <wait_port|0> <script_relpath_from_repo_root>
REM
REM   Waits until 127.0.0.1:<wait_port> accepts a TCP connection (pass 0 to skip
REM   the wait), then launches the repo venv python on <script_relpath>.
REM
REM   Used by start_all_wt.bat so each Windows Terminal tab waits for the
REM   schwab-proxy (:8100) before starting its service — preserving the same
REM   start ordering the multi-window start_all.bat had. The caller runs this
REM   under `cmd /k`, so the tab stays open (live logs) after the service exits.
setlocal
set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "WAITPORT=%~1"
set "SCRIPT=%~2"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    exit /b 1
)

cd /d "%ROOT%"

if not "%WAITPORT%"=="0" (
    echo [wait_and_run] waiting for dependency on 127.0.0.1:%WAITPORT% ...
    :waitloop
    timeout /t 1 /nobreak >nul
    "%PY%" "%ROOT%\tools\wait_http.py" --port %WAITPORT% --timeout 0 --label "the dependency" >nul 2>&1
    if errorlevel 1 goto waitloop
    echo [wait_and_run] dependency on :%WAITPORT% is up.
)

echo [wait_and_run] starting %SCRIPT% ...
echo.
"%PY%" "%SCRIPT%"
echo.
echo [wait_and_run] %SCRIPT% has exited. This tab stays open (cmd /k) — close it to dismiss.
