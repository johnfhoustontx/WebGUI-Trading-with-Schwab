@echo off
REM ===========================================================================
REM  trading.bat - drive the REMOTE trading stack from this Windows box.
REM
REM  THIS IS NOT A RETURN OF THE DELETED LAUNCHERS. The twelve .bat files the
REM  Linux migration removed STARTED processes on this machine and supervised
REM  them by hand -- WMI process hunts, storm-capped restart loops, hidden
REM  console relaunches. systemd owns the PIDs now. Every command below is a
REM  one-line systemctl sent over SSH; nothing here starts, supervises or
REM  restarts anything locally. It is a keyboard shortcut, in the same category
REM  as tools\open_webgui.ps1, which only forwards ports.
REM
REM  Host comes from %USERPROFILE%\.ssh\config:
REM      vps2-ts   tailnet route (default; survives the public IP changing)
REM      vps2      public IP, the fallback that works when Tailscale does not
REM  Override for one call:   set TRADING_HOST=vps2
REM
REM  ASCII ONLY, deliberately. cmd.exe mis-parses an LF-terminated batch file
REM  containing a non-ASCII byte under codepage 65001, and the .gitattributes
REM  rule that used to force CRLF on *.bat was deleted with the launchers. Keep
REM  this file 7-bit and the line endings stop mattering.
REM ===========================================================================

setlocal

if not defined TRADING_HOST set "TRADING_HOST=vps2-ts"
set "TARGET=trading-prod.target"
set "UNITPREFIX=trading-prod"

if "%~1"=="" goto usage
if /i "%~1"=="start"   goto do_start
if /i "%~1"=="stop"    goto do_stop
if /i "%~1"=="restart" goto do_restart
if /i "%~1"=="status"  goto do_status
if /i "%~1"=="health"  goto do_health
if /i "%~1"=="logs"    goto do_logs
if /i "%~1"=="tunnel"  goto do_tunnel
if /i "%~1"=="shell"   goto do_shell
goto usage

REM ---------------------------------------------------------------------------
:do_start
echo Starting %TARGET% on %TRADING_HOST% ...
ssh %TRADING_HOST% "systemctl --user start %TARGET%"
if errorlevel 1 goto failed
echo.
echo Waiting for the stack to answer (services bind a few seconds after they
echo report running, so a unit state is not proof it is serving) ...
ssh %TRADING_HOST% "for i in $(seq 1 30); do curl -s -m 3 -o /dev/null 127.0.0.1:8500/desk && break; sleep 2; done"
goto do_health

REM ---------------------------------------------------------------------------
:do_stop
echo.
echo This stops the WHOLE stack, including the web GUI - so there will be no
echo page left to restart from. Come back with:  trading start
echo.
choice /c YN /n /m "Stop %TARGET% on %TRADING_HOST%? [Y/N] "
if errorlevel 2 goto cancelled
echo Stopping ...
ssh %TRADING_HOST% "systemctl --user --no-block stop %TARGET%"
if errorlevel 1 goto failed
echo Stop job registered. Redis is a SYSTEM unit and keeps running - a --user
echo stop cannot reach it, so your cache and data are untouched.
goto end

REM ---------------------------------------------------------------------------
:do_restart
if "%~2"=="" (
    echo Restarting the whole stack ...
    ssh %TRADING_HOST% "systemctl --user restart %TARGET%"
    if errorlevel 1 goto failed
    goto do_health
)
call :unitname %~2
echo Restarting %UNIT% ...
ssh %TRADING_HOST% "systemctl --user restart %UNIT%"
if errorlevel 1 goto failed
ssh %TRADING_HOST% "systemctl --user list-units '%UNITPREFIX%*' --no-pager --no-legend"
goto end

REM ---------------------------------------------------------------------------
:do_status
ssh %TRADING_HOST% "systemctl --user list-units '%UNITPREFIX%*' --no-pager --no-legend; echo; systemctl --user list-units '%UNITPREFIX%*' --state=failed --no-pager --no-legend"
goto end

REM ---------------------------------------------------------------------------
:do_health
echo.
echo --- proxy :8100 ---
REM %% is an escaped percent. A single %% here is eaten by cmd.exe before curl
REM ever sees it, which is the batch metacharacter trap this repo documented.
ssh %TRADING_HOST% "curl -s -m 10 127.0.0.1:8100/health | python3 -m json.tool"
echo --- services and web GUI ---
ssh %TRADING_HOST% "for p in 8210 8211 8212 8213 8214 8215; do printf '  :%%s  ' $p; curl -s -m 8 -o /dev/null -w '%%{http_code}\n' 127.0.0.1:$p/health; done; printf '  webgui  '; curl -s -m 25 -o /dev/null -w '%%{http_code}\n' 127.0.0.1:8500/desk"
goto end

REM ---------------------------------------------------------------------------
:do_logs
if "%~2"=="" (
    echo Following ALL units. Ctrl-C to stop.
    ssh -t %TRADING_HOST% "journalctl --user -u '%UNITPREFIX%*' -f"
    goto end
)
call :unitname %~2
echo Following %UNIT%. Ctrl-C to stop.
ssh -t %TRADING_HOST% "journalctl --user -u %UNIT% -f"
goto end

REM ---------------------------------------------------------------------------
:do_tunnel
echo Forwarding 8500 (web GUI) and 8100 (proxy /auth, /health) from %TRADING_HOST%.
echo Both bind 127.0.0.1 on the server and have NO authentication, which is why
echo this is a tunnel and not a public URL. Leave this window open; closing it
echo closes the tunnel.
echo.
echo     web GUI      http://127.0.0.1:8500
echo     proxy auth   http://127.0.0.1:8100/auth
echo.
ssh -N -L 8500:127.0.0.1:8500 -L 8100:127.0.0.1:8100 %TRADING_HOST%
goto end

REM ---------------------------------------------------------------------------
:do_shell
ssh %TRADING_HOST%
goto end

REM ---------------------------------------------------------------------------
REM Map a friendly name to a unit. The six domain services carry a _svc suffix;
REM webgui and proxy do not.
:unitname
set "UNIT=%UNITPREFIX%-%~1_svc.service"
if /i "%~1"=="webgui" set "UNIT=%UNITPREFIX%-webgui.service"
if /i "%~1"=="proxy"  set "UNIT=%UNITPREFIX%-proxy.service"
if /i "%~1"=="backup" set "UNIT=%UNITPREFIX%-backup.service"
exit /b 0

REM ---------------------------------------------------------------------------
:usage
echo.
echo   trading ^<command^> [service]
echo.
echo     start              start the whole stack, then check it answers
echo     stop               stop the whole stack (confirms first)
echo     restart [service]  restart everything, or one service
echo     status             unit states, and anything failed
echo     health             proxy token state + every HTTP endpoint
echo     logs [service]     follow the journal (Ctrl-C to stop)
echo     tunnel             forward 8500 and 8100 to this PC
echo     shell              plain SSH session
echo.
echo   service is one of: sentiment options portfolio trade driver market
echo                      webgui proxy backup
echo.
echo   Examples:
echo     trading start
echo     trading logs options
echo     trading restart proxy
echo.
echo   Host: %TRADING_HOST%   (set TRADING_HOST=vps2 to use the public IP)
echo.
goto end

:failed
echo.
echo FAILED - ssh returned an error. Is %TRADING_HOST% reachable?
echo   Tailscale down?   set TRADING_HOST=vps2   and try again (public IP).
exit /b 1

:cancelled
echo Cancelled - nothing was stopped.
goto end

:end
endlocal
