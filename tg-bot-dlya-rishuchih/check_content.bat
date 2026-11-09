@echo off
setlocal
rem Perevirka content.yaml bez zapusku bota: podviinyi klik po failu.
cd /d "%~dp0"
title Perevirka content.yaml
set "VPY=%LOCALAPPDATA%\TgBotDlyaRishuchih\venv\Scripts\python.exe"
if exist "%VPY%" goto check
echo Spochatku odyn raz zapustit start_bot.bat - vin vstanovyt use potribne.
pause
exit /b 1

:check
"%VPY%" -m bot.check_content
echo.
pause
