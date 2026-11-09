@echo off
setlocal EnableExtensions
rem ==========================================================================
rem  Bot "Dlya rishuchyh" - zapusk na comp'iuteri: podviinyi klik po failu.
rem  Pershyi raz: stvoriuie seredovyshche Python, vstanovliuie biblioteky
rem  i vidkryvaie .env u Bloknoti - vstavte token bota pislia BOT_TOKEN=
rem  Seredovyshche Python i lokalna baza lezhat poza OneDrive:
rem  %LOCALAPPDATA%\TgBotDlyaRishuchih
rem ==========================================================================
cd /d "%~dp0"
title TG BOT DLYA RISHUCHIH

set "APPDIR=%LOCALAPPDATA%\TgBotDlyaRishuchih"
set "VENV=%APPDIR%\venv"
set "VPY=%VENV%\Scripts\python.exe"

if exist "%VPY%" goto deps

call :find_python
if not defined PY goto no_python
echo [1/3] Stvoriuiu seredovyshche Python - tilky pry pershomu zapusku...
if not exist "%APPDIR%" mkdir "%APPDIR%"
%PY% -m venv "%VENV%"
if errorlevel 1 goto venv_failed

:deps
echo [2/3] Pereviriaiu biblioteky - pershyi raz tse 1-2 khvylyny...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto pip_failed

if exist ".env" goto run
copy /y ".env.example" ".env" >nul
echo.
echo  Zaraz u Bloknoti vidkryietsia fail .env
echo  Vstavte token bota odrazu pislia BOT_TOKEN= bez probiliv i lapok,
echo  zberezhit fail - Ctrl+S - i zakryite Bloknot.
echo.
pause
notepad ".env"

:run
rem Lokalna baza - poza OneDrive, yakshcho v .env ne zadano DB_PATH
findstr /r /b /c:"DB_PATH=." ".env" >nul 2>nul
if errorlevel 1 set "DB_PATH=%APPDIR%\bot.db"
echo [3/3] Zapuskaiu bota. Zupynyty: Ctrl+C abo zakryty tse vikno.
echo.
"%VPY%" -m bot
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="2" echo  Bot ne zapustyvsia - prychyna napysana vyshche: fail .env abo content.yaml.
echo Bot zupyneno.
pause
exit /b %CODE%

:find_python
set "PY="
for %%V in (3.13 3.14 3.12 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -%%V"
  )
)
if defined PY exit /b 0
python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
exit /b 0

:no_python
echo.
echo  Ne znaishov Python 3.11-3.14 na tsomu comp'iuteri.
echo  1. Vidkryite https://www.python.org/downloads/windows/
echo  2. Zavantazhte "Windows installer (64-bit)" dlia Python 3.14 abo 3.13.
echo     Python 3.15 poky ne pidkhodyt: biblioteka aiogram yoho shche ne pidtrymuie.
echo  3. Pid chas vstanovlennia postavte galochku "Add python.exe to PATH".
echo  4. Zapustit tsei fail shche raz.
echo.
pause
exit /b 1

:venv_failed
echo.
echo  Ne vdalosia stvoryty seredovyshche Python u %VENV%
pause
exit /b 1

:pip_failed
echo.
echo  Ne vdalosia vstanovyty biblioteky. Pereviryte internet i zapustit fail shche raz.
echo  Yakshcho ne dopomohlo - vydalit papku %VENV% i sprobuite znovu.
pause
exit /b 1
