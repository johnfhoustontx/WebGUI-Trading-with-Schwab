@echo off
title Options Scanner - Single Scan
cd /d "%~dp0"
python scanner.py --once
pause
