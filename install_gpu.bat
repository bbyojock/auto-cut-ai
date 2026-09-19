@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Use the project virtual environment when it exists (run.bat creates it),
REM so packages land where app.py actually runs.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo ============================================================
echo  AutoCutAI - GPU Setup Assistant
echo ============================================================
echo.
echo This script checks your system, installs the required Python
echo packages, and tells you exactly what to do next. It NEVER
echo fails silently -- if something is missing, it explains why and
echo what to do about it. GPU acceleration is OPTIONAL: AutoCutAI
echo always works on CPU even if you skip this entirely.
echo.

set "ALL_OK=1"

REM ----------------------------------------------------------------
REM 1. Check Python
REM ----------------------------------------------------------------
echo [1/4] Checking Python...
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo   [FAIL] Python was not found on PATH.
    echo          Install Python 3.11 or newer from https://www.python.org/downloads/
    echo          and make sure to check "Add Python to PATH" during setup.
    set "ALL_OK=0"
    goto :end
) else (
    for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
    echo   [OK] Found Python !PYVER!
)

REM ----------------------------------------------------------------
REM 2. Check for an NVIDIA GPU
REM ----------------------------------------------------------------
echo.
echo [2/4] Checking for an NVIDIA GPU...
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo   [INFO] nvidia-smi was not found. Either you have no NVIDIA GPU,
    echo          or the driver is not installed yet.
    echo          AutoCutAI will use CPU mode automatically -- this is fine.
    echo          To use GPU mode later, install the latest NVIDIA driver from:
    echo          https://www.nvidia.com/Download/index.aspx
    set "HAS_GPU=0"
) else (
    echo   [OK] nvidia-smi found. Your GPU:
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
    set "HAS_GPU=1"
)

REM ----------------------------------------------------------------
REM 4. Check FFmpeg
REM ----------------------------------------------------------------
echo.
echo [3/4] Checking FFmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo   [FAIL] ffmpeg was not found on PATH.
    echo          AutoCutAI's Version 2 analysis pipeline needs it for audio
    echo          extraction. Download a build from:
    echo          https://www.gyan.dev/ffmpeg/builds/
    echo          then add its "bin" folder to your PATH and re-run this script.
    set "ALL_OK=0"
) else (
    echo   [OK] ffmpeg found.
)

REM ----------------------------------------------------------------
REM 4. Install Python packages
REM ----------------------------------------------------------------
echo.
echo [4/4] Installing required Python packages...
%PY% -m pip install --upgrade pip
if errorlevel 1 (
    echo   [FAIL] Could not upgrade pip. Check your internet connection and
    echo          Python installation, then re-run this script.
    set "ALL_OK=0"
    goto :end
)

%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo   [FAIL] Package installation failed. Scroll up for the exact error.
    echo          Common fixes: run this script as Administrator, or check
    echo          your internet connection / proxy settings.
    set "ALL_OK=0"
    goto :end
)
echo   [OK] Python packages installed.

if "!HAS_GPU!"=="1" (
    echo.
    echo Installing GPU-enabled CTranslate2/faster-whisper dependencies...
    %PY% -m pip install --upgrade ctranslate2 faster-whisper
    if errorlevel 1 (
        echo   [WARN] Could not reinstall ctranslate2/faster-whisper for GPU.
        echo          AutoCutAI will still work fine on CPU.
    ) else (
        echo   [OK] Whisper GPU dependencies installed.
    )
)

:end
echo.
echo ============================================================
if "!ALL_OK!"=="1" (
    echo  Setup finished. Run AutoCutAI with:  run.bat
    echo  Open the "Diagnostics" page inside AutoCutAI to confirm
    echo  exactly what mode (GPU or CPU^) Whisper will use.
) else (
    echo  Setup finished WITH WARNINGS. Read the messages above --
    echo  AutoCutAI may still run in CPU mode even if some GPU-only
    echo  steps failed. Nothing here was silently skipped.
)
echo ============================================================
echo.
pause
