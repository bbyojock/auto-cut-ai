#!/bin/bash
# macOS: Finder에서 이 파일을 더블클릭하면 터미널이 열리며 실행됩니다.
# (처음 실행 시 "확인되지 않은 개발자" 경고가 뜨면, 파일을 마우스 오른쪽 클릭 후
#  "열기"를 선택해서 한 번 더 실행해주세요.)

# 이 스크립트가 있는 폴더로 이동 (어디서 실행해도 항상 여기서 동작하게)
cd "$(dirname "$0")"

echo "============================================"
echo "  AutoCutAI 실행 준비 중..."
echo "============================================"
echo ""

# --- Python 설치 여부 확인 (python3 우선, 없으면 python) ---
PYTHON_CMD="python3"
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    PYTHON_CMD="python"
fi
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    echo "[오류] Python이 설치되어 있지 않은 것 같습니다."
    echo "README.md의 \"시작하기 전에 준비할 것\" 항목을 참고해서"
    echo "Python 3.11 이상을 먼저 설치해주세요."
    echo ""
    read -p "종료하려면 Enter를 누르세요..."
    exit 1
fi

# --- 가상환경이 없으면 처음 실행 -> 자동으로 만들고 설치 ---
if [ ! -f ".venv/bin/activate" ]; then
    echo "처음 실행이시네요. 필요한 프로그램을 자동으로 설치할게요."
    echo "(인터넷 상황에 따라 몇 분 걸릴 수 있습니다)"
    echo ""
    "$PYTHON_CMD" -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[오류] 가상환경 생성에 실패했습니다."
        read -p "종료하려면 Enter를 누르세요..."
        exit 1
    fi

    source .venv/bin/activate
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[오류] 필요한 패키지 설치에 실패했습니다. 인터넷 연결을 확인해주세요."
        read -p "종료하려면 Enter를 누르세요..."
        exit 1
    fi
    echo ""
    echo "설치가 끝났습니다. 프로그램을 실행합니다..."
    echo ""
else
    source .venv/bin/activate
fi

python app.py

echo ""
echo "프로그램이 종료되었습니다."
read -p "이 창을 닫으려면 Enter를 누르세요..."
