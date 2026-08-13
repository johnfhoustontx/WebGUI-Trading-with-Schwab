@echo off
REM Helper for start_webgui.bat: wait for the web GUI port to bind, then open a
REM browser at it. The PORT IS AN ARGUMENT (%1) rather than a literal, because
REM the two environments bind different ports and this helper used to hardcode
REM :8500 — from the DEV checkout it would have waited on prod's port forever
REM and then opened prod's web GUI. Defaults to 8500 only if called with no
REM argument, which preserves the old behavior for any hand-run.
set "WEBPORT=%~1"
if not defined WEBPORT set "WEBPORT=8500"

:waitweb
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',%WEBPORT%);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitweb
start "" "http://127.0.0.1:%WEBPORT%"
