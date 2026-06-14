@echo off
REM start_all.bat — Launch all Claude_is_the_Driver services
REM Run once each morning before market open (or leave running continuously).
REM Services started:
REM   1. OptionsAnalytics  port 8200  GEX/DEX/Charm snapshots
REM   2. Approval Server   port 8300  browser APPROVE/SKIP UI
REM   3. Morning Agent     9:28am ET  day grader + trade selector

echo ============================================================
echo  Claude is the Driver — Starting all services
echo ============================================================
echo.

REM Window 1: OptionsAnalytics (GEX/DEX/Charm — required for Bucket A)
start "OptionsAnalytics" cmd /k "cd /d D:\Schwab Test Project\OptionsAnalytics && call start.bat"

REM Pause to let OptionsAnalytics bind port 8200
timeout /t 5 /nobreak >nul

REM Window 2: Approval server (stays up all day, opens browser on trade)
start "Approval Server" cmd /k "cd /d "%~dp0" && python approval_server.py"

REM Pause to let approval server bind port 8300
timeout /t 3 /nobreak >nul

REM Window 3: Morning agent scheduler (fires at 9:28am ET each trading day)
start "Morning Agent" cmd /k "cd /d "%~dp0" && python morning_agent.py"

echo.
echo Services started:
echo   OptionsAnalytics : http://127.0.0.1:8200/options/snapshot/$SPX
echo   Approval server  : http://127.0.0.1:8300
echo   Morning agent    : scheduled 9:28am ET (Mon-Fri)
echo.
echo Services started — launcher exiting.
