@echo off
title Options Scanner - EOD Report
cd /d "%~dp0"
python eod_report.py %*
pause
