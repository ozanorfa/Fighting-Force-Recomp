@echo off
setlocal
rem Fighting Force Recompiled with a log: the desktop icon starts this file.
rem Like Play.bat, but the game's messages are kept in logs\last_run.txt
rem (each start replaces it), and when the game closes, Report.bat packs
rem the files to send if something went wrong into one ZIP in reports\.
rem Keep this window open while you play: closing it closes the game.
cd /d "%~dp0.."
rem Runtime overlay compiling off, as in every test of this build: the game
rem code is compiled at setup (GAME.EXE included, ~50 interpreted
rem instructions a frame), and the background compiler only costs CPU.
if not defined PSX_OVERLAY_AUTOCOMPILE_OFF set "PSX_OVERLAY_AUTOCOMPILE_OFF=1"
if not exist "build-net\FightingForce_Recompiled.exe" goto :missing
call "%~dp0Report.bat" prepare
if errorlevel 1 exit /b 1
title Fighting Force - keep this window open (closing it closes the game)
echo   Fighting Force is running. Keep this window open: closing it closes
echo   the game. The game's messages go to:
echo     "%CD%\logs\last_run.txt"
"build-net\FightingForce_Recompiled.exe" --game game_3p.toml --disc disc\FightingForce.cue --memcard-dir "%CD%\saves" > "logs\last_run.txt" 2>&1
set "RC=%ERRORLEVEL%"
echo.
echo   The game has closed.
call "%~dp0Report.bat" collect Play_with_log.bat %RC%
echo.
pause
exit /b 0
:missing
echo The game is not built yet: run Setup.bat first.
pause
exit /b 1
