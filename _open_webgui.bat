@echo off
REM Helper for start_webgui.bat: wait for :8500 to bind, then open the browser.
:waitweb
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8500);exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto waitweb
start "" "http://127.0.0.1:8500"
