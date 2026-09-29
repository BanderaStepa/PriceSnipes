@echo off
rem One-click setup for the White Build price checker. Double-click this file.
setlocal
cd /d "%~dp0"
title White Build price check - setup

echo.
echo === White Build price check: setup ===
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/windows/
  echo and TICK "Add python.exe to PATH" on the first screen. Then run this file again.
  echo.
  pause
  exit /b 1
)
python --version

echo.
echo [1/4] Creating the program's private Python folder (.venv)...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto failed

echo.
echo [2/4] Installing the parts it needs (takes a minute)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo [3/4] Downloading the browser it uses (takes a few minutes)...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed

echo.
echo [4/4] Creating the Desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
if errorlevel 1 goto failed

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo === Done! ===
echo A "White Build Price Check" icon is now on your Desktop.
echo Next: open it and click "Edit .env (Gmail)" to enter your Gmail address and app password.
echo.
pause
exit /b 0

:failed
echo.
echo *** Setup stopped because of an error (see the messages above). ***
echo Take a screenshot of this window and send it to Claude.
echo.
pause
exit /b 1
