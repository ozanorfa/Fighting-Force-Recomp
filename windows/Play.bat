@echo off
setlocal
rem Fighting Force Recompiled on this PC. The launcher opens first: Controls
rem and Settings are there; Play starts the game (up to three players with
rem enough controllers). Memory card: the saves folder, the same card a match
rem you host uses (tools\ff_netplay.py --saves).
cd /d "%~dp0.."
rem Runtime overlay compiling off, as in every test of this build: the game
rem code is compiled at setup (GAME.EXE included, ~50 interpreted
rem instructions a frame), and the background compiler only costs CPU.
if not defined PSX_OVERLAY_AUTOCOMPILE_OFF set "PSX_OVERLAY_AUTOCOMPILE_OFF=1"
if not exist "build-net\FightingForce_Recompiled.exe" goto :missing
start "" "build-net\FightingForce_Recompiled.exe" --game game_3p.toml --disc disc\FightingForce.cue --memcard-dir "%CD%\saves"
exit /b 0
:missing
echo The game is not built yet: run Setup.bat first.
pause
exit /b 1
