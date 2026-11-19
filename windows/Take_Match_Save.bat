@echo off
setlocal
rem After a match as a guest: make the match's campaign save your own memory
rem card, so you can host the next session. The match's copy
rem (saves\netplay\card1.mcd) is copied over your card 1 (saves\card1.mcd);
rem your old card is kept as a backup next to it. Shows both cards first.
cd /d "%~dp0.."
set "PY="
if exist "python\python.exe" set PY="%CD%\python\python.exe"
if defined PY goto :py_ok
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :py_ok
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto :no_python
:py_ok
%PY% tools\ff_match_save.py status
echo.
set "ANSWER="
set /p "ANSWER=Copy the last match's save over your card 1? (y/n) "
if /i not "%ANSWER%"=="y" goto :done
%PY% tools\ff_match_save.py import
:done
echo.
pause
exit /b 0
:no_python
echo Python 3 was not found: install it (tick "Add python.exe to PATH").
pause
exit /b 1
