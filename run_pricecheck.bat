@echo off
rem White Build price check - run by Task Scheduler every 4 hours.
rem Activates the venv, runs the script and appends all output to logs\run.log.
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
rem keep the log from growing forever: roll it over at ~5 MB
for %%F in (logs\run.log) do if %%~zF GTR 5000000 move /y logs\run.log logs\run.old.log >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PRICECHECK_STDOUT_LOG=1
call ".venv\Scripts\activate.bat"
echo ===== %date% %time% start ===== >> logs\run.log
python pricecheck.py %* >> logs\run.log 2>&1
set RC=%ERRORLEVEL%
echo ===== %date% %time% end (exit code %RC%) ===== >> logs\run.log
exit /b %RC%
