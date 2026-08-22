@echo off
setlocal enabledelayedexpansion
REM -- Monthly refit of the swing factor model (Phase 6) ------------------------
REM
REM Archives the current artifact + report under a DATED folder, re-runs the fit,
REM and diffs the new report against the prior one so a decay is visible without
REM anyone remembering to look.
REM
REM Why a .bat driven by a scheduled task rather than a service job: the fit must
REM stay un-importable by any service (it pulls 5 years of history for ~78
REM symbols and takes minutes), and dev runs with schedulers: False, so a service
REM job would sit inert.
REM
REM !! It refuses to run without the proxy. A fit against a dead proxy produces an
REM artifact from whatever partial history it managed, which would then SHIP as
REM the model - worse than not refitting at all.
REM
REM Schedule (once, elevated):
REM   schtasks /Create /TN "SwingModelRefit" /SC MONTHLY /D 1 /ST 19:00 ^
REM     /TR "\"D:\WebGUI Trading with Schwab\tools\refit_swing_model.bat\""

cd /d "%~dp0.."
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [refit] no venv at "%PY%" - aborting.
  exit /b 1
)

REM Resolve the proxy port from repo_paths rather than hardcoding it (the dev
REM checkout borrows prod's proxy, so the number is environment-dependent).
set "PORTFILE=%TEMP%\refit_port.txt"
"%PY%" -c "import sys;sys.path.insert(0,'.');import repo_paths;print(repo_paths.PROXY_PORT)" > "%PORTFILE%" 2>nul
set /p PROXY_PORT=<"%PORTFILE%"
del "%PORTFILE%" >nul 2>&1
if "%PROXY_PORT%"=="" set "PROXY_PORT=8100"

"%PY%" -c "import sys,urllib.request;urllib.request.urlopen('http://127.0.0.1:%PROXY_PORT%/health',timeout=5)" >nul 2>&1
if errorlevel 1 (
  echo [refit] proxy not answering on :%PROXY_PORT% - aborting rather than
  echo         fitting on whatever partial history a dead proxy returns.
  exit /b 1
)

for /f %%d in ('"%PY%" -c "import datetime;print(datetime.date.today().isoformat())"') do set "TODAY=%%d"
set "DATA=%CD%\trade-analyzer\data"
set "ARCHIVE=%DATA%\archive\%TODAY%"

if exist "%DATA%\swing_model.json" (
  if not exist "%ARCHIVE%" mkdir "%ARCHIVE%"
  copy /y "%DATA%\swing_model.json" "%ARCHIVE%\" >nul
  if exist "%DATA%\swing_model_report.md" (
    copy /y "%DATA%\swing_model_report.md" "%ARCHIVE%\" >nul
    copy /y "%DATA%\swing_model_report.md" "%TEMP%\swing_report_prev.md" >nul
  )
  echo [refit] archived the current artifact to %ARCHIVE%
) else (
  echo [refit] no existing artifact to archive - this is a first fit.
)

echo [refit] fitting...
cd /d "%CD%\trade-analyzer"
"%PY%" fit_swing_model.py
if errorlevel 1 (
  echo [refit] FIT FAILED - the previous artifact is untouched and still live.
  exit /b 1
)
cd /d "%~dp0.."

if exist "%TEMP%\swing_report_prev.md" (
  echo.
  echo [refit] report diff against the prior fit:
  "%PY%" tools\diff_swing_report.py "%TEMP%\swing_report_prev.md" "%DATA%\swing_model_report.md"
  del "%TEMP%\swing_report_prev.md" >nul 2>&1
)
echo [refit] done.
endlocal
