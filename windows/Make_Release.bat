@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Fighting Force Recompiled - make the friends' ZIP
rem Makes the prebuilt friends' ZIP (user decision 2026-11-18) with the
rem framework's release packager (packaging\release.toml, psxrecomp
rem docs\RELEASING_A_TITLE.md). Run it on the maintainer's Windows PC, in the
rem git checkout that Setup.bat made from the bundle
rem (C:\RecompDev\FightingForceRecomp), after Setup.bat built the game once:
rem the packager regenerates the game C from disc\FightingForce.cue.
rem Needs:
rem   - Python 3.11+ (py or python on PATH, or PSX_RELEASE_PYTHON);
rem   - MSYS2 MinGW-w64 with gcc, g++, objdump, cmake and ninja in
rem     C:\msys64\mingw64\bin (or set FF_TOOLCHAIN_BIN to another folder
rem     with the same five tools).
rem The result: dist\FightingForce3P-v<version>-playtest-windows-x64.zip and
rem its .sha256. It holds the compiled game: share it privately with friends
rem who own the disc, never publicly and never in git (dist\ is ignored).
rem Arguments pass to the packager (for example --allow-dirty to try
rem uncommitted changes; such a ZIP must not be shared).
cd /d "%~dp0.."
if not exist ".git" goto :no_git
if not exist "disc\FightingForce.cue" goto :no_disc
set "TC=C:\msys64\mingw64\bin"
if defined FF_TOOLCHAIN_BIN set "TC=%FF_TOOLCHAIN_BIN%"
for %%T in (gcc.exe g++.exe objdump.exe cmake.exe ninja.exe) do if not exist "%TC%\%%T" goto :no_tc
tasklist /fi "imagename eq FightingForce_Recompiled.exe" 2>nul | find /i "FightingForce_Recompiled.exe" >nul
if not errorlevel 1 goto :running
echo   Making the friends' ZIP. This builds the game again from your disc and
echo   boots it once, hidden and muted (profile training). Leave the PC alone
echo   until it ends with a success or a PACKAGING FAILED line.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "psxrecomp\tools\package_title_release.ps1" --channel playtest --platform windows --toolchain-bin "%TC%" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" goto :failed
echo   Done. The ZIP is in "%CD%\dist".
start "" explorer "%CD%\dist"
pause
exit /b 0
:failed
echo   The packager stopped (exit code %RC%). Select all the text in this
echo   window, copy it and send it: the line PACKAGING FAILED says why.
pause
exit /b %RC%
:no_git
echo   This folder is not a git checkout. Run Setup.bat from the bundle first:
echo   it makes C:\RecompDev\FightingForceRecomp; run this file from there.
pause
exit /b 1
:no_disc
echo   disc\FightingForce.cue is missing. Run Setup.bat first: it copies your
echo   disc there and builds the game once.
pause
exit /b 1
:no_tc
echo   The compiler tools are missing from "%TC%".
echo   Install MSYS2 (https://www.msys2.org/), then in its "MSYS2 MINGW64"
echo   window run:
echo     pacman -S --needed mingw-w64-x86_64-gcc mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja mingw-w64-x86_64-binutils
echo   Or set FF_TOOLCHAIN_BIN to a folder with gcc, g++, objdump, cmake and ninja.
pause
exit /b 1
:running
echo   Fighting Force is running. Close it (and its launcher) first.
pause
exit /b 1
