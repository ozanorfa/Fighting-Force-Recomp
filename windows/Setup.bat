@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Fighting Force Recompiled - setup
rem ------------------------------------------------------------------------
rem Double-click this file (or drop your "Fighting Force (USA).cue" on it).
rem The first time it looks for the disc by itself: put the folder with the
rem .cue and its 27 .bin files next to the extracted ZIP's folder.
rem
rem Players' ZIP: this file sits next to the folders FightingForceRecomp and
rem python. It copies them to C:\RecompDev\FightingForceRecomp and builds the
rem game there with YOUR disc. Nothing has to be installed: Python comes with
rem the ZIP, the compiler downloads itself once.
rem Bundle (developers): this file sits next to FightingForceRecomp.bundle;
rem it clones / updates the folder with Git (Git and Python needed).
rem
rem A newer ZIP or bundle: run its Setup.bat again. It updates the folder,
rem keeps your saves and disc path, and builds again.
rem Another folder: set FF_DIR=D:\Somewhere\FightingForceRecomp first.
rem ------------------------------------------------------------------------

set "HERE=%~dp0"
set "BUNDLE=%HERE%FightingForceRecomp.bundle"
set "SRC=%HERE%FightingForceRecomp"
set "DEST=C:\RecompDev\FightingForceRecomp"
if defined FF_DIR set "DEST=%FF_DIR%"
set "CUE=%~1"

echo.
echo   Fighting Force Recompiled - setup
echo   ==================================
echo   Install folder: "%DEST%"
echo.

set "MODE="
if exist "%SRC%\tools\ff_setup.py" set "MODE=zip"
if not defined MODE if exist "%BUNDLE%" set "MODE=bundle"
if not defined MODE goto :no_source

rem ---- 1. Python (and Git for the bundle) ----------------------------------
set "PYCMD="
if "%MODE%"=="zip" goto :py_zip
where git >nul 2>nul
if errorlevel 1 goto :no_git
py -3 --version >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto :py_ok
python --version >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD goto :no_python
goto :py_ok
:py_zip
if not exist "%HERE%python\python.exe" goto :no_zip_python
:py_ok

rem ---- 2. the disc (asked once, remembered for updates) --------------------
if defined CUE goto :check_cue
if exist "%DEST%\disc_source.txt" set /p CUE=<"%DEST%\disc_source.txt"
if not defined CUE goto :staged_disc
if exist "%CUE%" goto :check_cue
:staged_disc
rem An update whose original dump has moved: the install's own copy, which
rem the last successful build checked.
set "CUE=%DEST%\disc\FightingForce.cue"
if exist "%CUE%" goto :check_cue
:find_disc
rem First install: look for the disc near this file (this folder, the one
rem above it and the one above that, each with its subfolders): the first
rem .cue with this project's 27 .bin files beside it (see :try_cue).
call :find_cue
if not defined CUE goto :not_found
echo   Found your disc:
echo     "%CUE%"
goto :check_cue
:not_found
echo   Your disc was not found next to this folder.
echo.
:ask_cue
set "CUE="
echo   Drag your "Fighting Force (USA).cue" file into this window and press Enter.
echo   (All 27 .bin files of the dump must be in the same folder.)
set /p "CUE=  Disc .cue: "
if not defined CUE goto :ask_cue
set "CUE=%CUE:"=%"
:check_cue
rem A typed path may be relative or have no drive letter: make it full
rem here, from the folder Setup started in (the build runs in the install
rem folder). Every route to the disc passes this line.
for %%F in ("%CUE%") do set "CUE=%%~fF"
if not exist "%CUE%" goto :cue_missing
for %%F in ("%CUE%") do set "CUEDIR=%%~dpF"
set NBIN=0
for %%B in ("%CUEDIR%*.bin") do set /a NBIN+=1
if %NBIN% LSS 27 goto :few_bins
echo   Disc: "%CUE%"
echo.

rem ---- 3. the code ---------------------------------------------------------
tasklist /fi "imagename eq FightingForce_Recompiled.exe" 2>nul | find /i "FightingForce_Recompiled.exe" >nul
if not errorlevel 1 goto :game_running
if "%MODE%"=="bundle" goto :git_code
echo   Copying the game's code and Python ...
if /i "%SRC%"=="%DEST%" goto :copied
robocopy "%SRC%" "%DEST%" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul
if errorlevel 8 goto :failed
robocopy "%HERE%python" "%DEST%\python" /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul
if errorlevel 8 goto :failed
:copied
set PYCMD="%DEST%\python\python.exe"
set "PATH=%DEST%\python;%PATH%"
goto :build
:git_code
if exist "%DEST%\.git" goto :update
echo   Copying the code ...
git -c core.longpaths=true clone "%BUNDLE%" "%DEST%"
if errorlevel 1 goto :failed
git -C "%DEST%" config core.longpaths true
goto :build
:update
echo   Updating the code from the bundle ...
git -C "%DEST%" pull --ff-only "%BUNDLE%" master
if errorlevel 1 goto :failed

rem ---- 4. framework + build (tools\ff_setup.py) -----------------------------
:build
cd /d "%DEST%"
echo   Building the game. The first time takes a while: the compiler is
echo   downloaded once and the whole game is compiled for this PC.
echo.
%PYCMD% tools\ff_setup.py --disc "%CUE%"
if errorlevel 1 goto :failed
if not exist "%DEST%\build-net\FightingForce_Recompiled.exe" goto :failed

rem ---- 5. a desktop shortcut (the launcher: play, settings, netplay room) ---
rem It starts windows\Play_with_log.bat: every run keeps a log, and when the
rem game closes Report.bat packs the files to send into reports\. WindowStyle
rem 1 (a normal window) is set every time, because CreateShortcut keeps an
rem existing shortcut's settings, and a minimized start could start the game
rem window minimized too. %DEST:'=''% doubles any ' for PowerShell's '...'.
rem The icon: the title logo made from your disc (tools\ff_icon.py), else the exe's.
set "ICON=%DEST%\build-net\FightingForce_Recompiled.exe,0"
if exist "%DEST%\FightingForce.ico" set "ICON=%DEST%\FightingForce.ico,0"
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; $l=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Fighting Force.lnk'); $l.TargetPath='%DEST:'=''%\windows\Play_with_log.bat'; $l.WorkingDirectory='%DEST:'=''%'; $l.IconLocation='%ICON:'=''%'; $l.WindowStyle=1; $l.Save()" >nul 2>nul

echo.
echo   Done. Start the game with "Fighting Force" on your desktop
echo   (or "%DEST%\windows\Play_with_log.bat"). Online: NETPLAY - LAN / Direct IP.
echo   Keep its black window open while you play. When the game closes it
echo   packs the files to send if something went wrong into one ZIP.
echo   Other helpers in "%DEST%\windows":
echo     Host_Match.bat       start an online match without the launcher room
echo     Join_Match.bat       join one (player 2 or 3)
echo     Take_Match_Save.bat  after a match: take its save to host next time
echo     Report.bat           packs the last run's files to send into one ZIP
echo     Play.bat             the game without a log or report ZIP
echo.
start "" explorer "%DEST%\windows"
pause
exit /b 0

rem ---- messages ------------------------------------------------------------
:no_source
echo   Nothing to install next to this file: it needs the folder
echo   FightingForceRecomp (players' ZIP: extract the whole ZIP first) or
echo   FightingForceRecomp.bundle, in:
echo     "%HERE%"
goto :stop
:game_running
echo   Fighting Force is still running. Close the game (and its launcher),
echo   then run Setup.bat again.
goto :stop
:no_zip_python
echo   The python folder is missing next to this file: extract the whole ZIP
echo   (all folders), then run Setup.bat again.
goto :stop
:no_git
echo   Git for Windows is not installed. Install it with the default options:
echo     https://git-scm.com/downloads/win
echo   then run this file again.
goto :stop
:no_python
echo   Python 3 is not installed (or only the Microsoft Store shortcut is there).
echo   Install it from https://www.python.org/downloads/windows/ and tick
echo   "Add python.exe to PATH", then run this file again.
goto :stop
:cue_missing
echo   File not found: "%CUE%"
echo.
goto :ask_cue
:few_bins
echo   Only %NBIN% .bin files next to that .cue; the dump needs all 27 tracks.
echo.
goto :ask_cue
:failed
echo.
echo   Setup stopped at the step above. Select all the text in this window,
echo   copy it and send it along: it says what went wrong.
echo   (Updating while the game is open? Close the game, then run Setup.bat again.)
:stop
echo.
pause
exit /b 1

rem ---- disc search (called only) --------------------------------------------
:find_cue
set "CUE="
call :scan_root "%HERE%."
if not defined CUE call :scan_root "%HERE%.."
if not defined CUE call :scan_root "%HERE%..\.."
exit /b 0

:scan_root
if defined CUE exit /b 0
for %%C in ("%~f1\*.cue") do call :try_cue "%%~fC"
if defined CUE exit /b 0
for /d %%D in ("%~f1\*") do call :scan_dir "%%~fD"
exit /b 0

:scan_dir
if defined CUE exit /b 0
for %%C in ("%~f1\*.cue") do call :try_cue "%%~fC"
exit /b 0

:try_cue
rem This project's dump only, judged by the .cue's OWN tracks (Redump names
rem them "<cue name> (Track NN).bin"): 27 of them, track 1 at exactly
rem 71618400 bytes (game.toml [prepare_disc] known_sizes). Other games in the
rem same folder, another rip or renamed files fall through to the prompt.
if defined CUE exit /b 0
if not exist "%~dpn1 (Track 01).bin" exit /b 0
set "TRY_OK="
for %%B in ("%~dpn1 (Track 01).bin") do if "%%~zB"=="71618400" set "TRY_OK=1"
if not defined TRY_OK exit /b 0
set TRY_N=0
for %%B in ("%~dpn1 (Track *).bin") do set /a TRY_N+=1
if %TRY_N% GEQ 27 set "CUE=%~f1"
exit /b 0
