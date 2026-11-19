@echo off
setlocal
rem Fighting Force Recompiled (3 players): start the game.
rem The first start asks for your own Fighting Force (USA) disc: choose its
rem .cue file (all 27 .bin tracks must be next to it). The launcher remembers it.
cd /d "%~dp0"
rem Overlay code is precompiled into the game; no compiling while you play.
if not defined PSX_OVERLAY_AUTOCOMPILE_OFF set "PSX_OVERLAY_AUTOCOMPILE_OFF=1"
if not exist "FightingForce_Recompiled.exe" goto :missing
start "" "FightingForce_Recompiled.exe" --game game_3p.toml --memcard-dir "%CD%\saves"
exit /b 0
:missing
echo FightingForce_Recompiled.exe is not next to this file. Extract the whole
echo ZIP (right-click, Extract All) and start Play.bat from the new folder.
pause
exit /b 1
