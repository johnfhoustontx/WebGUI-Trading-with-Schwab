@echo off
REM ===========================================================================
REM  open_webgui.bat - double-click this to reach the trading web GUI.
REM
REM  Opens an SSH tunnel to the VPS and launches the browser at the local end:
REM
REM      this PC 127.0.0.1:8500  ->  (ssh -L)  ->  VPS 127.0.0.1:8500   web GUI
REM      this PC 127.0.0.1:8100  ->  (ssh -L)  ->  VPS 127.0.0.1:8100   proxy
REM
REM  WHY BOTH PORTS. The Schwab refresh token expires and is re-minted through
REM  the proxy's own paste-the-URL form at http://127.0.0.1:8100/auth.
REM  Forwarding only the GUI made that page unreachable exactly when it was
REM  needed, and the failure looked like a broken proxy rather than a missing
REM  forward.
REM
REM  WHY A TUNNEL AND NOT A URL. Both services bind 127.0.0.1 on the VPS and
REM  have NO AUTHENTICATION OF ANY KIND. That is correct for a desk-side app and
REM  it is the whole problem on a server: the GUI can open paper positions, apply
REM  rescue adjustments, arm the autonomous driver and stop the entire stack, and
REM  the proxy holds the Schwab credentials. The tunnel gives you both while they
REM  stay bound to loopback at each end, authenticated by your SSH key.
REM
REM  Do NOT "simplify" this by changing either bind to 0.0.0.0.
REM
REM  THE WINDOW STAYS OPEN while you use the app. Closing it closes the tunnel.
REM  That is deliberate: a forgotten background tunnel is an access path nobody
REM  remembers granting. It is also the single most common way this appears
REM  broken -- ssh -N prints nothing and looks hung, so the window gets closed
REM  and the browser stops reaching the page.
REM
REM  ASCII only: cmd.exe mis-parses an LF-terminated batch file containing a
REM  non-ASCII byte under codepage 65001.
REM ===========================================================================

setlocal

if not defined TRADING_HOST set "TRADING_HOST=vps2-ts"
set "WEBPORT=8500"
set "PROXYPORT=8100"

title Trading web GUI tunnel - %TRADING_HOST%

echo.
echo   Trading web GUI tunnel
echo   ----------------------
echo   host        %TRADING_HOST%
echo   web GUI     http://127.0.0.1:%WEBPORT%
echo   proxy auth  http://127.0.0.1:%PROXYPORT%/auth
echo.

REM --- Refuse to start a second tunnel on top of a live one -------------------
REM ssh would fail to bind and exit, and the error scrolls past. Worse, if some
REM OTHER process holds 8500 the browser would open onto whatever that is.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %WEBPORT% -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo   Port %WEBPORT% is ALREADY in use on this PC.
    echo.
    echo   Either a tunnel is already open - in which case just browse to
    echo   http://127.0.0.1:%WEBPORT% - or another process holds the port.
    echo   To see which:
    echo.
    echo       Get-NetTCPConnection -LocalPort %WEBPORT% -State Listen
    echo.
    pause
    exit /b 1
)

REM --- Open the browser once the GUI actually ANSWERS -------------------------
REM Backgrounded so ssh can hold the foreground.
REM
REM It waits for an HTTP 200, NOT for the local port to accept a connection.
REM That distinction is the whole point: ssh binds the local end the instant it
REM connects, whether or not anything is listening on the FAR end. A TCP check
REM therefore always passes, and the browser opens onto a dead forward showing a
REM generic error, while the real cause --
REM
REM     channel 3: open failed: connect failed: Connection refused
REM
REM -- scrolls past in this window. That is exactly what happened the first time
REM the stack was left stopped overnight. An HTTP 200 can only come from the
REM web GUI itself, so it cannot be faked by the forward existing.
start "" /b powershell -NoProfile -Command "$u='http://127.0.0.1:%WEBPORT%/desk'; for ($i=0; $i -lt 60; $i++) { try { if ((Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { Start-Process 'http://127.0.0.1:%WEBPORT%'; exit } } catch { } Start-Sleep -Milliseconds 750 }; Write-Host ''; Write-Host '  The tunnel is UP, but nothing answered on the VPS.'; Write-Host '  The stack is almost certainly stopped. In another window run:'; Write-Host ''; Write-Host '      trading start'; Write-Host ''; Write-Host '  then reload http://127.0.0.1:%WEBPORT% - this tunnel stays usable.'; Write-Host ''"

echo   Opening tunnel. The browser follows in a moment.
echo   ssh prints nothing while it holds the forwards open - that is normal.
echo.
echo   *** CLOSE THIS WINDOW to disconnect. ***
echo.

ssh -N -L %WEBPORT%:127.0.0.1:%WEBPORT% -L %PROXYPORT%:127.0.0.1:%PROXYPORT% %TRADING_HOST%

REM Reached only when ssh exits: closed, dropped, or refused.
echo.
echo   Tunnel closed (ssh exit code %errorlevel%).
echo.
echo   If it exited immediately, the usual causes are:
echo     - Tailscale is down. Try the public IP:  set TRADING_HOST=vps2
echo     - the VPS is off or unreachable
echo     - the key at %%USERPROFILE%%\.ssh\vps is missing
echo.
pause
endlocal
