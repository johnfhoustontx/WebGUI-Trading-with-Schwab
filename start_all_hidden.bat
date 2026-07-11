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
REM  It works by relaunching itself HIDDEN (so not even this console
REM  lingers — you'll see at most a brief flash), then running
REM  start_all_wt.bat in its no-window mode. Memurai (the Redis service)
REM  is left as-is; make sure it's running.
REM
REM  To STOP everything: run stop_all.bat, or open the web GUI and use
REM  More > Terminate (Memurai stays up).
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
exit /b
