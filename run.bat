@echo off
REM ==========================================================
REM  AutoCutAI launcher (Windows)
REM  - ASCII only on purpose: cmd.exe mis-parses non-ASCII text
REM    in .bat files depending on the console code page.
REM  - Must be saved with CRLF line endings (see .gitattributes).
REM ==========================================================
setlocal
cd /d "%~dp0"
title AutoCutAI

echo ============================================
echo   AutoCutAI - starting...
echo ============================================
echo.

REM --- 1. Find a usable Python (3.11+) ---------------------
set "BASE_PY="

py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "BASE_PY=py -3"
if defined BASE_PY goto python_found

python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "BASE_PY=python"
if defined BASE_PY goto python_found

echo [ERROR] Python 3.11 or newer was not found.
echo         Install it from https://www.python.org/downloads/
echo         and check "Add python.exe to PATH" in the installer.
echo         If Python is already installed, turn off the Microsoft Store
echo         "python.exe" alias: Settings - Apps - Advanced app settings -
echo         App execution aliases.
echo.
pause
exit /b 1

:python_found

REM --- 2. Create the virtual environment if needed ---------
REM We never "activate" it. We call .venv\Scripts\python.exe directly,
REM so the global Python can never be used by accident.
set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto create_venv
"%VENV_PY%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto venv_ready

echo The existing .venv is broken. Recreating it...
rmdir /s /q ".venv"

:create_venv
echo First run: creating virtual environment (.venv)...
%BASE_PY% -m venv .venv
if errorlevel 1 goto venv_failed
if not exist "%VENV_PY%" goto venv_failed
echo.

:venv_ready

REM --- 3. Install packages if needed -----------------------
REM A copy of requirements.txt is kept inside .venv after a successful
REM install. If requirements.txt changes later, packages are reinstalled.
set "STAMP=.venv\requirements.installed.txt"

if not exist "%STAMP%" goto install_deps
fc /b "requirements.txt" "%STAMP%" >nul 2>nul
if errorlevel 1 goto install_deps
goto run_app

:install_deps
echo Installing required packages. This can take a few minutes...
echo.
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed
copy /y "requirements.txt" "%STAMP%" >nul
echo.
echo Setup finished. Starting AutoCutAI...
echo.

REM --- 4. Run the app --------------------------------------
:run_app
"%VENV_PY%" app.py
if errorlevel 1 goto app_failed
exit /b 0

:venv_failed
echo [ERROR] Could not create the virtual environment.
echo         Try deleting the .venv folder and run this file again.
echo.
pause
exit /b 1

:pip_failed
echo.
echo [ERROR] Package installation failed.
echo         Check your internet connection and the messages above,
echo         then run this file again.
echo.
pause
exit /b 1

:app_failed
echo.
echo [ERROR] AutoCutAI exited with an error. See the messages above.
echo.
pause
exit /b 1
