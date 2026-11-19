@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Fighting Force Recompiled: the files to send when something went wrong.
rem Double-click it: it packs the last run's files into one ZIP in reports\
rem and shows that ZIP. Send the ZIP to whoever sent you the game.
rem Play_with_log.bat calls it itself:
rem   Report.bat prepare            before the game starts: stop if the game
rem                                 is already open; move an old
rem                                 psx_crash.txt to logs\previous_crash.txt
rem   Report.bat collect NAME CODE  after the game: write how it ended to
rem                                 the log, then pack the ZIP
rem The ZIP holds whichever exist of logs\last_run.txt,
rem psx_last_run_report.json, psx_crash.txt and psx_game_version.txt (no game data).
rem reports\ keeps the newest 20 ZIPs.
cd /d "%~dp0"
if /i "%~1"=="prepare" goto :prepare
if /i "%~1"=="collect" goto :collect
call :pack
echo.
pause
exit /b 0

:prepare
tasklist /fi "imagename eq FightingForce_Recompiled.exe" 2>nul | find /i "FightingForce_Recompiled.exe" >nul
if not errorlevel 1 goto :running
if not exist "logs" mkdir "logs"
rem psx_crash.txt is written only when the game really crashes and is never
rem removed: moved aside here, a psx_crash.txt after this run is from it.
if exist "psx_crash.txt" move /y "psx_crash.txt" "logs\previous_crash.txt" >nul
exit /b 0
:running
echo   Fighting Force is already running. Close it (and its launcher)
echo   first, then start it again.
pause
exit /b 1

:collect
if not exist "logs" mkdir "logs"
>>"logs\last_run.txt" echo [%~2] the game ended with exit code %~3
call :pack
exit /b 0

:pack
if not exist "reports" mkdir "reports"
set "ZIP="
rem The name from PowerShell: the PC's name with only letters, digits and
rem hyphens, and the time, so no character in it can break this script.
for /f "usebackq delims=" %%Z in (`powershell -NoProfile -Command "'reports\FightingForce_' + ($env:COMPUTERNAME -replace '[^A-Za-z0-9-]','_') + '_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip'"`) do set "ZIP=%%Z"
if not defined ZIP goto :no_zip
powershell -NoProfile -Command "$f = @('logs\last_run.txt','psx_last_run_report.json','psx_crash.txt','psx_game_version.txt') | Where-Object { Test-Path -LiteralPath $_ }; if ($f) { Compress-Archive -LiteralPath $f -DestinationPath '%ZIP%' -Force }" >nul 2>nul
powershell -NoProfile -Command "Get-ChildItem -LiteralPath 'reports' -Filter 'FightingForce_*.zip' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 20 | Remove-Item -Force" >nul 2>nul
if not exist "%ZIP%" goto :no_zip
echo   If something went wrong, send this file:
echo     "%CD%\%ZIP%"
start "" explorer /select,"%CD%\%ZIP%"
exit /b 0
:no_zip
echo   Could not pack a report ZIP. Send these files instead, if they exist:
echo     "%CD%\logs\last_run.txt"
echo     "%CD%\psx_last_run_report.json"
echo     "%CD%\psx_crash.txt"
if exist "logs\last_run.txt" start "" explorer /select,"%CD%\logs\last_run.txt"
exit /b 0
