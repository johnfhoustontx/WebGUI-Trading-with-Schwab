@echo off
REM restart_one.bat <kill_port|0> <wait_port|0> <name> <script_relpath_from_repo_root>
REM
REM   Windowless single-component (re)start used by the System Status page's
REM   Restart buttons. Frees <kill_port> (taskkills whatever is LISTENING on it -
REM   clears a wedged process), waits for <wait_port> (0 = skip), then launches the
REM   venv python on <script> HIDDEN, with stdout/stderr redirected to
REM   logs\<name>.out.log / .err.log.
REM
REM   Meant to be spawned with CREATE_NO_WINDOW (see webgui/pages/status.py) so
REM   nothing flashes. Uses `ping` for its sleeps (not `timeout`) so it works in a
REM   hidden console with no interactive stdin.
REM
REM   KEEP THIS FILE CRLF AND ASCII-ONLY. Measured 2026-08-15: with LF-only
REM   line endings AND a non-ASCII byte anywhere in the file AND a console
REM   codepage of 65001 (UTF-8 - what PowerShell uses here; Git Bash is 437),
REM   cmd.exe resumes parsing 2 bytes into the NEXT line. Line 2 then reads as
REM   `M restart_one.bat <kill_port|0> ...`, the `<` is taken as a redirection,
REM   and the whole script dies on line 2 with:
REM       < was unexpected at this time.
REM   Removing ANY ONE of the three conditions fixes it, so this file carries
REM   two of the three: .gitattributes pins the CRLF, and the comments above
REM   stay ASCII. Guarded by tools/tests/test_batch_line_endings.py.
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
REM The dependency wait below runs tools\wait_http.py, so it needs an
REM interpreter. Fall back to PATH rather than abort: this script runs
REM WINDOWLESS from the Status page Restart buttons, where an abort is silent
REM and a probe that can never run would loop forever.
set "PYPROBE=%PY%"
if not exist "%PYPROBE%" set "PYPROBE=python"
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
    "%PY%" "%ROOT%\tools\wait_http.py" --port %WAITPORT% --timeout 0 --label "the dependency" >nul 2>&1
    if errorlevel 1 goto waitdep
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "H_PY=%PYW%"
if not exist "%PYW%" set "H_PY=%PY%"
powershell -NoProfile -Command "Start-Process -FilePath '%H_PY%' -ArgumentList '%SCRIPT%' -WorkingDirectory '%ROOT%' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\logs\%NAME%.out.log' -RedirectStandardError '%ROOT%\logs\%NAME%.err.log'"
