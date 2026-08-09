@echo off
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    echo Create it first:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.lock
    pause
    exit /b 1
)

REM --- Ports and identity come from repo_paths, never from a literal here. This
REM     file used to hardcode :8500 in its title, banner and browser helper, which
REM     was correct until a second environment existed: run from the DEV checkout
REM     it would start the web GUI on :9500 (main.py reads its own port) while
REM     announcing :8500 and opening a browser at prod's. The process was right
REM     and every word around it was wrong.
REM     Emitted as `set` lines into a temp batch and CALLed, rather than read
REM     back through `for /f "usebackq"`. That form strips the quotes around an
REM     interpreter path containing spaces, so "D:\WebGUI Trading Prod\.venv\..."
REM     dies as: 'D:\WebGUI' is not recognized. Measured, not theorised. The
REM     emitted `set` lines are deliberately UNQUOTED — a port and PROD/DEV have
REM     no spaces, and quoting them would put a double quote inside the -c
REM     argument, which walks straight back into the same trap.
REM
REM     The Python below uses CONCATENATION, never %-formatting: `%` is a batch
REM     metacharacter, so cmd rewrites `'set X=%s' % val` into
REM     `'set X= val` — an unterminated string — before Python ever sees it.
REM     Measured. `%%` would escape it, but no-percent-at-all cannot regress.
set "_NSENV=%TEMP%\_neuralstrike_env_%RANDOM%.bat"
"%PY%" -c "import repo_paths as r; print('set WEBPORT=' + str(r.NICEGUI_PORT)); print('set PROXYPORT=' + str(r.PROXY_PORT)); print('set ENVNAME=' + r.ENV_NAME.upper())" > "%_NSENV%" 2>nul
call "%_NSENV%" >nul 2>&1
del "%_NSENV%" >nul 2>&1
if not defined WEBPORT (
    echo Could not read the web GUI port from repo_paths.
    pause
    exit /b 1
)

title Schwab Web GUI %ENVNAME% (:%WEBPORT%)

REM --- Refuse if this environment's web GUI is already listening. Scoped to the
REM     web GUI alone (--only webgui): this launcher starts ONE process, and
REM     starting it while the services run is a normal thing to do, so the
REM     whole-stack check would refuse something perfectly correct.
"%PY%" tools\check_stack_down.py --only webgui
if errorlevel 1 (
    echo.
    echo Stop it first with stop_all.bat, or use the web GUI's More ^> Terminate page.
    pause
    exit /b 1
)

echo ============================================
echo   Schwab Web GUI [%ENVNAME%]   http://127.0.0.1:%WEBPORT%
echo ============================================
echo.
echo NOTE: the web gui reads market data through the
echo proxy on :%PROXYPORT%. Start it if you need live data
echo (in DEV that proxy belongs to the PROD checkout).
echo.

REM --- spawn a helper that waits for the port to bind, then opens the browser ---
start "open browser" /min cmd /c "%~dp0_open_webgui.bat" %WEBPORT%

echo Starting NiceGUI web app on :%WEBPORT% ... (keep this window open)
"%PY%" webgui\main.py
