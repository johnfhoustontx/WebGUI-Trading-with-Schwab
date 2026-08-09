@echo off
REM run_flow_delta_instrumentation.bat
REM
REM   Wrapper for the Monday post-close instrumentation run (see
REM   tools\flow_delta_instrumentation.py). Exists so Task Scheduler has one
REM   quoting-safe thing to invoke instead of a python.exe + args + redirect
REM   command line, which schtasks mangles.
REM
REM   ROOT is derived from this script's own location, so the checkout it lives
REM   in is the checkout it measures. Output goes to logs\flow_delta_instr.log;
REM   the report itself lands under
REM   options-scanner\data\flow_delta_instrumentation\<date>\.
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
echo ==== %DATE% %TIME% ==== >> "%ROOT%\logs\flow_delta_instr.log"
"%PY%" -X utf8 "%ROOT%\tools\flow_delta_instrumentation.py" >> "%ROOT%\logs\flow_delta_instr.log" 2>&1
echo exit=%ERRORLEVEL% >> "%ROOT%\logs\flow_delta_instr.log"
endlocal
