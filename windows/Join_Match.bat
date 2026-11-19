@echo off
setlocal
rem Join a friend's 3-player match over Radmin VPN as player 2 or 3 (the two
rem guests take different numbers). Asks for the host's Radmin VPN address.
rem On a command line: Join_Match.bat 26.1.2.3 2 [extra options]
rem The game's messages go to logs\last_run.txt; when the game closes,
rem Report.bat packs the files to send if something went wrong.
set "WIN=%~dp0"
cd /d "%~dp0.."
call :find_python
if not defined PY goto :no_python
if not exist "build-net\FightingForce_Recompiled.exe" goto :missing
set "HOSTIP=%~1"
set "SEAT=%~2"
if not defined HOSTIP set /p "HOSTIP=Host's Radmin VPN address (26.x.x.x): "
if not defined HOSTIP goto :no_host
set "HOSTIP=%HOSTIP: =%"
:ask_seat
if not defined SEAT set /p "SEAT=Your player number (2 or 3): "
set "SLOT="
if "%SEAT%"=="2" set "SLOT=1"
if "%SEAT%"=="3" set "SLOT=2"
if defined SLOT goto :seat_ok
echo The player number must be 2 or 3.
set "SEAT="
goto :ask_seat
:seat_ok
set "HOSTADDR=%HOSTIP%:7777"
echo %HOSTIP% | find ":" >nul
if not errorlevel 1 set "HOSTADDR=%HOSTIP%"
rem shift also moves this script's own path: Report.bat is called through WIN.
shift
shift
call "%WIN%Report.bat" prepare
if errorlevel 1 exit /b 1
echo Joining %HOSTADDR% as player %SEAT% ... Keep this window open.
%PY% tools\ff_netplay.py join --slot %SLOT% --host %HOSTADDR% --log logs\last_run.txt %1 %2 %3 %4 %5 %6
set "RC=%ERRORLEVEL%"
call "%WIN%Report.bat" collect Join_Match.bat %RC%
pause
exit /b 0

:find_python
set "PY="
rem the players' ZIP brings its own Python (the install folder's python\)
if exist "python\python.exe" set PY="%CD%\python\python.exe"
if defined PY exit /b 0
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY exit /b 0
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
exit /b 0
:no_python
echo Python 3 was not found: install it (tick "Add python.exe to PATH").
pause
exit /b 1
:no_host
echo No address given.
pause
exit /b 1
:missing
echo The game is not built yet: run Setup.bat first.
pause
exit /b 1
