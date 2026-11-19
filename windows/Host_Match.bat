@echo off
setlocal
rem Host a 3-player match over Radmin VPN: you are player 1. Give the other
rem two your Radmin VPN address (26.x.x.x). The game starts when both have
rem joined. Extra options can follow on a command line, for example
rem   Host_Match.bat --fixed-delay 5
rem (every player must then use the same option).
rem The game's messages go to logs\last_run.txt; when the game closes,
rem Report.bat packs the files to send if something went wrong.
cd /d "%~dp0.."
call :find_python
if not defined PY goto :no_python
if not exist "build-net\FightingForce_Recompiled.exe" goto :missing
call "%~dp0Report.bat" prepare
if errorlevel 1 exit /b 1
echo Hosting a match: you are player 1. Keep this window open.
echo Tell the others your Radmin VPN address (26.x.x.x). Waiting for them ...
%PY% tools\ff_netplay.py host --log logs\last_run.txt %*
set "RC=%ERRORLEVEL%"
call "%~dp0Report.bat" collect Host_Match.bat %RC%
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
:missing
echo The game is not built yet: run Setup.bat first.
pause
exit /b 1
