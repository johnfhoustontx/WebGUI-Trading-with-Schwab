@echo off
REM restart_one.bat <kill_port|0> <wait_port|0> <name> <script_relpath_from_repo_root>
REM
REM   Windowless single-component (re)start used by the System Status page's
REM   Restart buttons. Frees <kill_port> (taskkills whatever is LISTENING on it —
REM   clears a wedged process), waits for <wait_port> (0 = skip), then launches the
REM   venv python on <script> HIDDEN, with stdout/stderr redirected to
REM   logs\<name>.out.log / .err.log.
REM
REM   Meant to be spawned with CREATE_NO_WINDOW (see webgui/pages/status.py) so
REM   nothing flashes. Uses `ping` for its sleeps (not `timeout`) so it works in a
REM   hidden console with no interactive stdin.
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
set "KILLPORT=%~1"
set "WAITPORT=%~2"
set "NAME=%~3"
set "SCRIPT=%~4"

if not "%KILLPORT%"=="0" (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":%KILLPORT% " ^| findstr /i "LISTENING"') do taskkill /F /PID %%P >nul 2>&1
    ping -n 2 127.0.0.1 >nul
)

if not "%WAITPORT%"=="0" (
    :waitdep
    ping -n 2 127.0.0.1 >nul
    powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',%WAITPORT%);exit 0}catch{exit 1}" >nul 2>&1
    if errorlevel 1 goto waitdep
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "H_PY=%PYW%"
if not exist "%PYW%" set "H_PY=%PY%"
powershell -NoProfile -Command "Start-Process -FilePath '%H_PY%' -ArgumentList '%SCRIPT%' -WorkingDirectory '%ROOT%' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\logs\%NAME%.out.log' -RedirectStandardError '%ROOT%\logs\%NAME%.err.log'"
