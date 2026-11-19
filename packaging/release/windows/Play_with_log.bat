@echo off
setlocal
rem Fighting Force Recompiled (3 players): start the game and keep its
rem messages in logs\last_run.txt. When the game closes, this window packs a
rem report ZIP (Report.bat) to send if something went wrong.
cd /d "%~dp0"
if not defined PSX_OVERLAY_AUTOCOMPILE_OFF set "PSX_OVERLAY_AUTOCOMPILE_OFF=1"
if not exist "FightingForce_Recompiled.exe" goto :missing
call "%~dp0Report.bat" prepare
if errorlevel 1 exit /b 1
title Fighting Force - keep this window open (closing it closes the game)
echo   Fighting Force is running. Keep this window open: closing it closes
echo   the game. The game's messages go to:
echo     "%CD%\logs\last_run.txt"
"FightingForce_Recompiled.exe" --game game_3p.toml --memcard-dir "%CD%\saves" > "logs\last_run.txt" 2>&1
set "RC=%ERRORLEVEL%"
echo.
echo   The game has closed.
call "%~dp0Report.bat" collect Play_with_log.bat %RC%
echo.
pause
exit /b 0
:missing
echo FightingForce_Recompiled.exe is not next to this file. Extract the whole
echo ZIP (right-click, Extract All) and start Play_with_log.bat from the new folder.
pause
exit /b 1
