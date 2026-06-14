@echo off
title SPX/SPY/QQQ Options Scanner
echo ============================================================
echo   Options Scanner - SPX / SPY / QQQ
echo   Credit Spread Scanner (0-DTE + Swing)
echo   Press Ctrl+C to stop
echo ============================================================
echo.
cd /d "%~dp0"
python scanner.py %*
pause
