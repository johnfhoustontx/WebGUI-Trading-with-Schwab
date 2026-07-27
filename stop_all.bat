@echo off
title NeuralStrike - stop all
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Could not find venv python at "%PY%".
    pause
    exit /b 1
)

REM Stops the proxy + 5 services + web GUI by listening port (ports come from
REM repo_paths -> config\ports.toml). Memurai (:6379) is left running — it's a
REM shared Windows service, not something this repo starts. The web GUI is killed
REM last so this still works when spawned from the GUI's own Terminate button.
"%PY%" "%~dp0tools\stop_all.py"

echo.
echo This window will close in a few seconds...
timeout /t 4 /nobreak >nul
