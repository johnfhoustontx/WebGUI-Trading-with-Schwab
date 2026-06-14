@echo off
title Sector Rotation Assessment
cd /d "%~dp0"

:: Check if proxy is running (required - the tool fetches all data through it)
py -3.11 -c "import requests; requests.get('http://127.0.0.1:8100/health',timeout=2)" 2>nul
if errorlevel 1 (
    echo ERROR: Schwab Proxy not detected on :8100.
    echo        Start Launch_Proxy.bat first, then re-run this.
    echo.
    pause
    exit /b 1
)
echo Schwab Proxy detected - fetching sector data (takes ~15-20s)...
echo.

py -3.11 sector_rotation_assessment.py %*
if errorlevel 1 (
    echo.
    echo If Python was not found, ensure Python 3.11 is installed and in PATH
)

echo.
pause
