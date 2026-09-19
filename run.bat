@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM 이 스크립트가 있는 폴더로 이동 (어디서 더블클릭해도 항상 여기서 실행되게)
cd /d "%~dp0"

echo ============================================
echo   AutoCutAI 실행 준비 중...
echo ============================================
echo.

REM --- Python 설치 여부 확인 ---
where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않은 것 같습니다.
    echo README.md의 "시작하기 전에 준비할 것" 항목을 참고해서
    echo Python 3.11 이상을 먼저 설치해주세요.
    echo ^(설치 시 "Add Python to PATH" 체크박스를 꼭 켜주세요^)
    echo.
    pause
    exit /b 1
)

REM --- 가상환경이 없으면 처음 실행 -> 자동으로 만들고 설치 ---
if not exist ".venv\Scripts\activate.bat" (
    echo 처음 실행이시네요. 필요한 프로그램을 자동으로 설치할게요.
    echo ^(인터넷 상황에 따라 몇 분 걸릴 수 있습니다^)
    echo.
    python -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )

    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 필요한 패키지 설치에 실패했습니다. 인터넷 연결을 확인해주세요.
        pause
        exit /b 1
    )
    echo.
    echo 설치가 끝났습니다. 프로그램을 실행합니다...
    echo.
) else (
    call .venv\Scripts\activate.bat
)

python app.py

echo.
echo 프로그램이 종료되었습니다.
pause
