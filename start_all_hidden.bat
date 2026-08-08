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

REM Second pass (relaunched hidden): do the actual work.
if /i "%~1"=="__hidden" goto run

REM First pass (the visible double-click console): relaunch myself hidden,
REM then close this console immediately.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '__hidden' -WindowStyle Hidden"
exit /b

:run
cd /d "%~dp0"
call "%~dp0start_all_wt.bat" nowindow
call :launch_hud
exit /b

REM ------------------------------- subroutines -------------------------------

:launch_hud
REM The HUD is deliberately NOT launched through start_all_wt.bat's
REM :launch_hidden helper, for two reasons:
REM   1. That helper passes -WindowStyle Hidden. The HUD is a desktop window
REM      you are meant to look at, so hiding it would defeat the point. Here
REM      pythonw suppresses only the CONSOLE and the GUI still appears.
REM   2. It is not a service. It binds no port, so stop_all.bat cannot see it
REM      and the web GUI's Status page cannot health-check it — grouping it
REM      with the eight port-bound processes would imply otherwise.
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
