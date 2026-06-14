@echo off
title Schwab API Proxy (port 8100)
cd /d "%~dp0"
echo ============================================
echo   Schwab API Proxy - Centralized Token Mgr
echo   http://127.0.0.1:8100
echo ============================================
echo.
echo Starting proxy... (keep this window open)
echo.
python schwab_proxy.py
if errorlevel 1 (
    echo.
    echo Proxy exited with error. Check logs\schwab_proxy.log
    echo.
    echo Required packages: pip install fastapi uvicorn requests
    pause
)
