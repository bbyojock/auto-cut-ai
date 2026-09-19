# AutoCutAI — 개발 문서

이 문서는 프로젝트 구조, 아키텍처, 버전별 구현 내역을 다루는 **개발자용** 문서입니다.
설치하고 그냥 써보고 싶다면 [README.md](README.md)를 먼저 보세요.

---

# AutoCutAI

Gemini, OpenRouter, OpenAI, Claude, 로컬 OpenAI 호환 API(Ollama/LM Studio/vLLM 등) 중
원하는 AI 프로바이더를 자유롭게 조합해 쓸 수 있는 ChatGPT 스타일의 데스크톱 애플리케이션입니다.
CustomTkinter 기반 GUI, 프로바이더별 독립 설정과 무제한 API 키 자동 로테이션, 실시간 스트리밍
응답, GPU 자동 감지 및 안전한 CPU 폴백을 지원하는 Whisper 전사, 세션 한정 대화 메모리를
제공합니다.

> 이 저장소는 `AutoCutAI`라는 더 큰 프로젝트(DaVinci Resolve 연동 자동 영상 편집 도구)의
> 개발 진행분입니다. 현재 v1(멀티 프로바이더 채팅 GUI), v2(비디오 분석 파이프라인),
> v3(AI 편집 브레인, GUI에 완전히 연결됨), v3.5(EditPlan 검증/시뮬레이션/규칙 엔진),
> v4(스트리밍 진행률/취소, 프롬프트 최적화, EditPlan 캐시/저장·불러오기, 사람이 읽기 쉬운
> EditPlan 뷰어, AI 요청 로그/토큰 사용량, 벤치마크, 내보내기, 진단 확장까지),
> v5(DaVinci Resolve Studio API 연동 + Resolve Free XML export를 통한 실제 컷 편집)에 이어
> v5.3(폴더 단위 일괄 임포트/분석, 다큐멘터리 자동 배열, 예능/버라이어티 편집 프로필,
> 파일명 기반 멀티캠 그룹핑)와 v5.4(멀티캠 자동 앵글 전환 편집 + 다중 소스 XML
> 내보내기)에 이어 v5.5(COMPRESS 배속이 XML 내보내기에 실제로 반영됨)와 v5.6(AI가
> 영상 분위기·배경음악 스타일도 함께 추천)까지 구현되어 있습니다.

## 주요 기능

- **멀티 프로바이더 아키텍처**: Gemini, OpenRouter, OpenAI, Claude, 로컬 OpenAI 호환 API를
  기본 제공하며, Settings에서 프로바이더를 자유롭게 추가/삭제/활성화/비활성화할 수 있습니다.
  각 프로바이더는 독립적인 API 키 목록, Base URL, 모델명(항상 자유 텍스트, 고정 드롭다운
  없음), 타임아웃/재시도 설정을 가지며, `ProviderFactory`가 `provider_type`만으로 동적으로
  프로바이더 인스턴스를 생성하므로 새 프로바이더 추가 시 `EditPlanner`/`ChatService`는 한 줄도
  바뀌지 않습니다. 자세한 내용은 아래 "멀티 프로바이더 아키텍처" 절을 참고하세요.
- **무제한 API 키 관리 + 자동 로테이션**: 프로바이더마다 키를 원하는 만큼 추가/삭제/순서
  변경할 수 있고, 429(Rate Limit), 할당량 초과(Quota Exceeded), 잘못된 키(Invalid Key),
  타임아웃이 발생하면 자동으로 다음 키로 전환합니다. 일시적 오류(429/타임아웃)는 쿨다운 후
  재사용, 영구적 오류(할당량 초과/잘못된 키)는 해당 실행 세션 동안 제외됩니다.
- **Test Connection**: 각 프로바이더 카드에서 API 키·Base URL·모델 가용성을 실제 최소
  요청으로 검증하고, 원시 트레이스백 없이 사용자 친화적인 메시지로 결과를 보여줍니다.
- **대화 메모리(세션 한정)**: 현재 세션의 모든 대화를 기억하지만, 프로그램을 종료하면
  디스크에 저장하지 않고 사라집니다.
- **스트리밍 응답**: AI 응답을 생성되는 즉시 화면에 실시간으로 표시합니다.
- **설정 저장 + 자동 마이그레이션**: 모든 프로바이더 설정, Whisper 백엔드/모델, 테마,
  창 크기를 `config/config.json`에 저장합니다. 이전 버전(단일 Gemini 설정)의 설정 파일도
  자동으로 새 멀티 프로바이더 구조로 마이그레이션되어 기존 키/모델이 그대로 보존됩니다.
  오래된 Gemini 모델명이 감지되면 Settings에 마이그레이션 배너가 뜨고, 사용자가 명시적으로
  저장하기 전까지는 아무것도 덮어쓰지 않습니다.
- **Whisper GPU 런타임 매니저 — 절대 크래시하지 않음**: CUDA DLL을 직접 검사하지 않고
  CTranslate2의 `get_supported_compute_types("cuda")`와 CUDA 장치 보고를 source of truth로
  사용합니다. CTranslate2가 CUDA를 지원하면 cuBLAS/cuDNN 상태를 사용 가능으로 보고 GPU를
  사용하며, 실제 모델 로드/전사에서 실패할 때만 정확한 오류와 함께 CPU로 재시도합니다.
- **Runtime Diagnostics 페이지**: Python/FFmpeg/Whisper/GPU/cuBLAS/cuDNN/VRAM/프로바이더/
  설정 상태를 신호등(🟢🟡🔴)으로 한눈에 보여주고, 리포트를 클립보드에 복사할 수 있습니다.
  원시 파이썬 트레이스백은 절대 사용자에게 노출되지 않습니다.
- **ChatGPT 스타일 GUI**: 좌측 사이드바(Home / AI Chat / AI Editor / Settings / Diagnostics /
  Logs), 채팅 영역, 입력창, 전송 버튼, 하단 상태 표시줄로 구성되어 있습니다.
- **(v2) 비디오 분석 파이프라인**: mp4/mov/mkv/avi 임포트 → 메타데이터 감지(길이/fps/해상도/
  총 프레임 수) → ffmpeg 오디오 추출 → faster-whisper 자동 언어 감지 및 타임스탬프 전사 →
  설정 가능한 간격의 미리보기 프레임 추출까지 이어지는 순수 분석 파이프라인입니다.
  아직 영상을 편집하거나 DaVinci Resolve와 연동하지 않습니다 — 자세한 내용은 아래
  "Version 2: 비디오 분석 파이프라인" 절을 참고하세요.
- **(v3) AI 편집 브레인 — 앱에 완전히 연결됨**: v2의 `AnalysisResult`(트랜스크립트)를
  "프로페셔널 유튜브 편집자" 페르소나 프롬프트로 감싸 활성 AI 프로바이더에 보내고, 응답
  JSON을 검증해 `EditPlan`(구간별 keep/remove 결정 + 신뢰도 + 근거 + 경고)으로 만듭니다.
  GUI의 **AI Editor** 페이지에서 비디오 선택 → 분석 → EditPlan 생성 → 결과 표시까지 바로
  사용할 수 있습니다. 아직 영상을 자르거나 렌더링하거나 DaVinci Resolve에 연결하지
  않습니다 — "Version 3: AI 편집 브레인" 절을 참고하세요.
- **(v3.5) EditPlan 품질/안전성 강화**: 겹침·중복·초단편 클립·타임라인 공백·잘못된
  timestamp를 자동으로 검증하고 가능하면 복구하는 `EditPlanValidator`, 영상을 건드리지
  않고 최종 길이/컷 수/평균 클립 길이 등을 계산하는 `EditSimulator`, 프롬프트에 하드코딩하지
  않고 keep/remove 규칙과 점수를 자유롭게 구성할 수 있는 `EditingRules`, 게이밍/브이로그/
  튜토리얼/팟캐스트/리액션/숏폼 중에서 고를 수 있는 프롬프트 스타일이 포함됩니다 —
  "Version 3.5" 절을 참고하세요.

## 요구 사항

- Python 3.11 이상
- OS: Windows / macOS / Linux (CustomTkinter가 지원하는 모든 플랫폼)
- **ffmpeg** (시스템에 설치되어 PATH에 등록되어 있어야 함) — Version 2의 오디오 추출 단계에서 사용됩니다.
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt-get install ffmpeg`
  - Windows: https://ffmpeg.org/download.html 에서 받아 PATH에 추가
- **GPU는 완전히 선택 사항입니다.** CUDA가 없거나 불완전해도 AutoCutAI는 자동으로 CPU
  모드로 전환되어 정상 동작합니다 — 자세한 내용은 아래 "GPU 가속 설치 가이드" 절 참고.

## 설치

```bash
cd AutoCutAI
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 실행

```bash
python app.py
```

처음 실행하면 `config/config.json`이 자동으로 생성되고, Gemini 프로바이더가 기본
활성화된 상태로 5개 프로바이더(Gemini/OpenRouter/OpenAI/Claude/Local AI) 항목이
미리 준비됩니다. **Settings** 페이지에서 원하는 프로바이더에 API 키를 추가하고 모델명을
입력한 뒤 "Active"로 선택 → 저장하면 **AI Chat**과 **AI Editor** 페이지 양쪽에 바로
반영됩니다. **Diagnostics** 페이지에서 GPU/CPU, FFmpeg, 프로바이더 상태를 언제든
확인할 수 있습니다.

GUI 없이 전체 파이프라인만 커맨드라인에서 검증하고 싶다면:

```bash
python scripts/analyze_and_plan.py path/to/video.mp4
python scripts/analyze_and_plan.py path/to/video.mp4 --target-length 120 --interval 3
```

이 스크립트는 GUI와 동일하게 `config/config.json`에 저장된 "Active" 프로바이더/모델/키와
Whisper 설정을 그대로 사용하며, `AnalysisPipeline.run() → EditPlanner.create_edit_plan()
→ print(plan)` 전체 흐름을 한 번에 검증합니다.

## 멀티 프로바이더 아키텍처

앱이 특정 AI 서비스에 종속되지 않습니다. Gemini는 계속 완전히 동작하며, 다음 프로바이더가
기본 제공됩니다 (Settings에서 자유롭게 추가/삭제 가능):

| 프로바이더 | provider_type | 기본 Base URL |
|---|---|---|
| Gemini | `gemini` | `https://generativelanguage.googleapis.com` |
| OpenRouter | `openai_compatible` | `https://openrouter.ai/api/v1` |
| OpenAI | `openai_compatible` | `https://api.openai.com/v1` |
| Claude | `anthropic` | `https://api.anthropic.com` |
| Local AI (Ollama/LM Studio/vLLM) | `openai_compatible` | `http://localhost:11434/v1` |

- **모델명은 항상 자유 텍스트**입니다 (`gemini-3.6-flash`, `anthropic/claude-sonnet-4.5`,
  `openai/gpt-5`, `deepseek/deepseek-v3`, `qwen/qwen3-coder` 등 무엇이든). 고정 드롭다운은
  없으며, 새 모델이 나와도 앱 업데이트 없이 바로 사용할 수 있습니다.
- **Base URL도 항상 자유 텍스트**이며 하드코딩되지 않습니다. Groq, DeepSeek, 다른 로컬
  서버 등 OpenAI 호환 API라면 무엇이든 Base URL/모델/키만 바꿔서 그대로 사용할 수 있습니다.
- **`ProviderFactory`는 provider-agnostic**합니다: `provider_type`("gemini" /
  "openai_compatible" / "anthropic")별로 생성 함수를 한 번만 등록해두면, 서로 다르게
  설정된 몇 개의 provider_id든 전부 같은 코드로 만들어집니다. `EditPlanner`/`ChatService`는
  `AIProvider` 추상 인터페이스에만 의존하므로 새 프로바이더 종류를 추가해도 변경할 필요가
  없습니다.
- 각 프로바이더는 **독립적인 API 키 목록(무제한, 자동 로테이션/순서 변경 가능)**, Base URL,
  모델, 활성화 여부, 타임아웃, 재시도 횟수를 가집니다.
- **Test Connection**: Settings의 각 프로바이더 카드에서 실제로 최소 요청을 보내 API 키·
  Base URL·모델 가용성을 한 번에 검증합니다. 실패해도 원시 트레이스백이 아니라 사람이 읽을
  수 있는 메시지만 표시됩니다.
- **설정 마이그레이션**: v1~v3.5에서 쓰던 예전 `config.json`(Gemini 전용 설정)을 그대로
  불러와도 자동으로 새 멀티 프로바이더 구조로 변환되며, 기존 키/모델은 그대로 보존됩니다.
- **오래된 모델 감지**: Gemini 모델명이 알려진 구버전(`gemini-1.5-flash` 등)이면 Settings에
  경고 배너와 "Use suggested model" 버튼이 나타납니다. 버튼을 눌러도 입력창 값만 바뀔 뿐,
  "Save Settings"를 직접 눌러야만 실제로 반영됩니다 — 사용자 확인 없이는 아무것도
  덮어쓰지 않습니다.

새 OpenAI 호환 프로바이더(예: Groq)를 추가하는 방법은 코드를 건드릴 필요 없이 Settings에서
"+ Add Provider"로 provider_type을 `openai_compatible`로 선택하고 Base URL/모델/키만
입력하면 끝입니다.

## GPU 가속 설치 가이드

**GPU 가속은 완전히 선택 사항입니다.** CUDA가 없거나, 있어도 일부 라이브러리가
빠져 있으면 AutoCutAI는 자동으로 CPU 모드로 전환해 계속 동작합니다 — 절대 크래시하지
않습니다. Settings의 Whisper Backend를 `Auto`(기본값)로 두면 아래 순서로 자동
판단합니다:

```
Auto → CTranslate2 CUDA 장치/compute type 확인
     → 지원하면 GPU 사용
     → 지원하지 않으면 이유를 로그에 남기고 CPU 사용
```

실제 모델 로드 또는 추론에서 CUDA 오류가 발생해도, AutoCutAI는 원래 오류와 스택 트레이스를
로그에 남긴 뒤 자동으로 CPU로 전환해 **한 번 더 재시도**합니다. `Auto`/`GPU`/`CPU` 중 선택은 Settings의 Whisper
섹션에서, Whisper 모델명(`tiny`/`base`/`small`/`medium`/`large-v3` 등, 항상 자유
텍스트)도 같은 곳에서 설정합니다.

### CPU 모드 (기본값, 별도 설치 불필요)

`requirements.txt`만 설치하면 끝입니다. 모든 기능이 완전히 동작하며, 영상 길이에 비례해
전사 시간이 다소 걸릴 뿐입니다.

### GPU 모드

**최소 요구 사항**
- NVIDIA GPU (Compute Capability 6.0 이상, Pascal 세대 이후)
- VRAM 4GB 이상 (`tiny`/`base`/`small` 모델 기준)
- NVIDIA 드라이버 525 이상

**권장 요구 사항**
- NVIDIA GPU (Compute Capability 7.5 이상, Turing 세대 이후 — RTX 20xx/30xx/40xx 등)
- VRAM 8GB 이상 (`medium`/`large-v3` 모델까지 여유 있게)
- NVIDIA 드라이버 최신 버전

**단계별 CUDA 설치**
1. https://www.nvidia.com/Download/index.aspx 에서 GPU에 맞는 최신 드라이버 설치
2. https://developer.nvidia.com/cuda-downloads 에서 **CUDA Toolkit 12.x** 설치
3. 설치 후 새 터미널을 열고 `nvidia-smi`를 실행해 GPU/드라이버/CUDA 버전이 보이는지 확인

**단계별 cuDNN 설치**
1. https://developer.nvidia.com/cudnn 에서 (NVIDIA 계정 필요) 사용 중인 CUDA 버전에 맞는
   **cuDNN 9.x**를 다운로드
2. Windows: 압축을 풀어 `bin`/`include`/`lib` 내용을 CUDA Toolkit 설치 폴더(예:
   `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.x`)에 병합하고, CUDA의
   `bin` 폴더가 PATH에 있는지 확인
3. Linux: 배포판의 `cudnn` 패키지를 설치하거나 NVIDIA가 제공하는 `.deb`/`.tar.xz`를 사용

**GPU 가속이 실제로 동작하는지 확인하는 방법**

AutoCutAI를 실행하고 **Diagnostics** 페이지에서 "Run Diagnostics"를 누르세요. GPU가
완전히 지원되면 다음과 같이 보입니다:

```
[OK] GPU: 1 CUDA device(s) detected, compute capability 8.6.
[OK] cuBLAS: Available.
[OK] cuDNN: Available.
[OK] Whisper Backend: CUDA / float16 (setting: auto)
```

Windows에서는 `install_gpu.bat`을 더블클릭하면 Python/GPU/CUDA/FFmpeg를 자동으로
점검하고 필요한 패키지를 설치합니다. CUDA capability는 실행 시 CTranslate2가 직접 판단합니다.
이 스크립트는 절대 조용히 실패하지 않고, 무엇이 왜 실패했는지
항상 화면에 표시합니다.

**흔한 문제 해결**

| 증상 | 원인 | 해결 방법 |
|---|---|---|
| Diagnostics에 "No NVIDIA GPU detected" | GPU가 없거나 드라이버 미설치 | GPU가 없다면 정상입니다 (CPU 모드 사용). GPU가 있다면 드라이버를 설치하세요. |
| GPU를 선택했는데 계속 CPU로 동작 | CTranslate2가 CUDA 장치 또는 compute type을 보고하지 않음 | 로그의 `ctranslate2 CUDA capability check failed` 원인을 확인하세요. |

**예상되는 Diagnostics 출력 (GPU 없음, 정상)**

```
[OK] Python: Python 3.12.3
[OK] FFmpeg: ffmpeg version 6.1.1 ...
[OK] Whisper: faster-whisper installed (CTranslate2 4.8.2).
[WARNING] GPU: No NVIDIA GPU detected.
[OK] cuBLAS: Not applicable (no GPU detected).
[OK] cuDNN: Not applicable (no GPU detected).
[OK] Whisper Backend: CPU / int8 (setting: auto)
```

이는 오류가 아니라 "이 컴퓨터는 GPU가 없으니 CPU를 쓰겠다"는 정상적인 안내입니다.

## 프로젝트 구조

```
AutoCutAI/
├── app.py                      # 진입점 (컴포지션 루트) — 모든 의존성을 조립하고 GUI를 실행
├── requirements.txt
├── install_gpu.bat             # Windows GPU 설치 점검/도우미 스크립트 (선택 사항)
├── scripts/
│   └── analyze_and_plan.py     # (v2+v3) CLI로 전체 파이프라인 검증: pipeline.run() → EditPlanner
├── ai/                         # AI 프로바이더 추상화 계층 + (v3/v3.5) AI 편집 브레인
│   ├── base_provider.py        # AIProvider 인터페이스 (+ test_connection 공통 구현)
│   ├── gemini_provider.py      # Gemini 구현체 (키 로테이션 + 스트리밍 + base_url)
│   ├── openai_compatible_provider.py  # OpenRouter/OpenAI/Local 등 OpenAI 호환 API 공용 구현체
│   ├── claude_provider.py      # Anthropic Claude Messages API 구현체
│   ├── key_manager.py          # 다중 API 키 라운드로빈 관리자
│   ├── provider_factory.py     # provider_type 기반 동적 생성 팩토리 (register_type/create_from_settings)
│   ├── prompt_templates.py     # 편집자 페르소나 / JSON 출력 계약 / (v3.5) 콘텐츠 스타일 6종
│   ├── context_builder.py      # AnalysisResult → 토큰 절약형 컨텍스트
│   ├── prompt_builder.py       # 컨텍스트+규칙+스타일+계약을 하나의 프롬프트로 조립
│   ├── response_parser.py      # AI의 JSON 응답 스키마 검증 + EditPlan 변환
│   ├── editing_rules.py        # (v3.5) 설정 가능한 keep/remove 규칙 엔진
│   ├── editing_scorer.py       # (v3.5) 세그먼트별 점수 계산 (진단/로깅용)
│   ├── edit_plan_validator.py  # (v3.5) EditPlan 검증 + 자동 복구
│   ├── edit_simulator.py       # (v3.5) 영상 수정 없이 EditPlan 효과 계산
│   └── edit_planner.py         # 전체 단계를 조율하는 오케스트레이터 (v4: create_edit_plan_streaming 추가)
├── config/                     # 영속 설정
│   ├── settings.py             # 설정 스키마 + 레거시 설정 자동 마이그레이션 + 모델 마이그레이션 제안
│   └── config_manager.py       # JSON 파일 읽기/쓰기
├── core/                       # 애플리케이션 핵심 로직
│   ├── session.py              # 세션 한정 대화 메모리
│   ├── app_context.py          # 의존성 컨테이너 (chat_service, edit_planner, v4: ai_request_log_service/edit_plan_cache_service/edit_plan_io_service/edit_generation_service 등)
│   ├── cancellation.py         # (v4) CancellationToken (취소 기능)
│   └── exceptions.py           # 예외 계층 (v4: OperationCancelledError 추가)
├── models/                     # 데이터 모델
│   ├── message.py               # 대화 메시지
│   ├── api_key.py               # API 키 상태 모델
│   ├── provider_settings.py     # 프로바이더별 독립 설정 (id/type/keys/base_url/model/enabled)
│   ├── connection_test_result.py# Test Connection 결과
│   ├── gpu_runtime_status.py     # GPU/CUDA 런타임 상태 + Whisper 디바이스 결정
│   ├── runtime_diagnostics.py    # Diagnostics 페이지의 신호등 체크/리포트 모델
│   ├── video_project.py         # (v2) 임포트된 비디오 + 메타데이터
│   ├── transcript.py            # (v2) Transcript / TranscriptSegment
│   ├── frame.py                 # (v2) Frame / FrameCollection
│   ├── analysis_result.py       # (v2) 파이프라인 최종 산출물
│   ├── edit_plan.py             # (v3) EditPlan / EditPlanSegment
│   ├── editing_rule.py          # (v3.5) EditingRule
│   ├── edit_simulation_result.py# (v3.5) EditSimulationResult
│   ├── provider_info.py         # (v3.5) ProviderInfo (프로바이더 레지스트리 메타데이터)
│   ├── generation_progress.py   # (v4) GenerationStage / ProgressEvent (진행률 창)
│   ├── ai_request_log.py        # (v4) AIRequestLogEntry (AI 요청 로그/토큰 사용량)
│   └── benchmark.py             # (v4) StageTiming / BenchmarkReport
├── video/                       # (v2) 비디오 분석 파이프라인
│   ├── video_importer.py        # 1단계: 임포트 + 메타데이터 감지
│   ├── audio_extractor.py       # 2단계: ffmpeg로 오디오 추출
│   ├── whisper_runtime_manager.py # GPU/CUDA/cuBLAS/cuDNN 검증 + 안전한 CPU 폴백 결정
│   ├── transcriber.py           # 3단계: faster-whisper 전사 (GPU 실패 시 자동 CPU 재시도)
│   ├── frame_extractor.py       # 4단계: 구간별 미리보기 프레임 추출
│   └── analysis_pipeline.py     # 4단계 전체를 조율하는 오케스트레이터
├── services/                   # UI와 코어 로직 사이의 서비스 계층
│   ├── chat_service.py          # 대화 전송/스트리밍 오케스트레이션
│   ├── diagnostics_service.py   # Runtime Diagnostics 리포트 생성 (v4: Whisper Model/Connection Status 추가)
│   ├── logging_service.py       # 중앙 집중식 로깅
│   ├── token_usage_service.py   # (v4) 토큰/비용 추정 + 프롬프트 절감률 계산
│   ├── streaming_json_parser.py # (v4) 스트리밍 중 EditPlan JSON 증분 파싱
│   ├── ai_request_log_service.py# (v4) AI 요청 구조화 로그 (Logs 페이지 "AI Requests" 탭)
│   ├── edit_plan_cache_service.py # (v4) 영상별 EditPlan 캐시
│   ├── edit_plan_io_service.py    # (v4) *.editplan.json 저장/불러오기
│   ├── edit_generation_service.py # (v4) 캐시 조회 → 파이프라인 → AI 생성 오케스트레이터
│   └── export_service.py          # (v4) EditPlan을 JSON/TXT/Markdown으로 내보내기
├── ui/                         # CustomTkinter GUI
│   ├── main_window.py           # 최상위 윈도우 + 페이지 라우팅
│   ├── widgets/sidebar.py       # 좌측 네비게이션 사이드바
│   ├── widgets/progress_dialog.py      # (v4) AI 진행률 창 (+ 취소 버튼)
│   ├── widgets/prompt_debug_window.py  # (v4) 프롬프트 디버그 창
│   └── pages/                   # home / chat / edit_plan / settings / diagnostics / logs 페이지
├── utils/                      # 공용 유틸리티 (상수, 파일 헬퍼)
├── davinci/                     # (예약됨) 향후 DaVinci Resolve 연동
├── assets/, logs/               # 정적 리소스 / 로그 파일 저장 위치
```

## Version 2: 비디오 분석 파이프라인

아직 GUI에는 연결되어 있지 않은 순수 백엔드 파이프라인입니다 (v3에서 GUI/편집 로직과
통합 예정). 코드로 직접 실행하는 예시:

```python
from video.analysis_pipeline import AnalysisPipeline

pipeline = AnalysisPipeline()
result = pipeline.run("my_video.mp4", frame_interval_seconds=5.0)

print(result.video.duration_seconds, result.video.fps)
print(result.transcript.language)
print(result.transcript.to_formatted_text())   # "00:01.220\nHello everyone.\n\n..."
print(len(result.frames), "frames extracted")
```

파이프라인 각 단계는 처리 시작 시 로그를 남깁니다:

```
Loading Video...
Extracting Audio...
Running Whisper...
Extracting Frames...
Analysis Complete.
```

- 오디오 추출은 시스템에 설치된 `ffmpeg` 바이너리를 사용하며, 임시 WAV 파일은
  OS 임시 디렉터리 아래 전용 폴더(`utils.file_utils.get_temp_dir()`)에 안전하게 저장되고
  전사가 끝나면 자동으로 삭제됩니다(`keep_audio_file=True`로 유지 가능).
- Whisper 모델은 첫 사용 시 한 번만 로드되어 `WhisperTranscriber` 인스턴스에 캐시됩니다.
- 프레임은 전체 프레임이 아니라 `frame_interval_seconds`(기본 5초)마다 하나씩만
  JPEG로 추출되며, 간격은 호출마다 자유롭게 조절할 수 있습니다.
- 파이프라인 도중 오류가 발생하면(`core.exceptions.AutoCutAIError` 계층) 이미 만들어진
  임시 오디오 파일을 정리한 뒤 예외를 다시 던집니다.

## Version 3: AI 편집 브레인

v2의 `AnalysisResult`를 입력받아 "어떻게 편집할지"만 결정하고, **영상을 자르거나
렌더링하거나 DaVinci Resolve에 연결하지는 않습니다.** 파이프라인:

```
AnalysisResult → ContextBuilder → PromptBuilder → AIProvider → ResponseParser → EditPlan
```

**앱에 완전히 연결되어 있습니다.** `app.py`가 `ChatService`와 정확히 같은 provider
인스턴스, 정확히 같은 `config.gemini.selected_model`로 `EditPlanner`를 만들어
`AppContext.edit_planner`로 노출합니다 — 모델 이름이 하드코딩된 곳은
`utils/constants.py`의 `DEFAULT_GEMINI_MODEL` 단 한 곳뿐이며, 나머지는 전부 Settings에
저장된 값을 그대로 읽습니다. GUI의 **AI Editor** 페이지에서 바로 사용할 수 있고,
프로그램에서 직접 쓰고 싶다면:

```python
from ai.edit_planner import EditPlanner
from ai.gemini_provider import GeminiProvider
from ai.key_manager import APIKeyManager
from config.config_manager import ConfigManager
from utils.constants import DEFAULT_GEMINI_MODEL
from video.analysis_pipeline import AnalysisPipeline

config = ConfigManager().load()          # Settings에 저장된 키/모델을 그대로 사용
analysis = AnalysisPipeline().run("my_video.mp4")

provider = GeminiProvider(APIKeyManager(config.gemini.api_keys or ["YOUR_GEMINI_API_KEY"]))
planner = EditPlanner(provider=provider, model=config.gemini.selected_model or DEFAULT_GEMINI_MODEL)

plan = planner.create_edit_plan(analysis, target_length_seconds=120)

print(plan.confidence, plan.reasons, plan.warnings)
for segment in plan.keep_segments:
    print(segment.start_seconds, segment.end_seconds, segment.reason)

plan.to_json()  # 완전히 직렬화 가능 (파일 저장, Logs 화면 등에 사용 가능)
```

같은 흐름을 커맨드라인에서 바로 검증하려면 `python scripts/analyze_and_plan.py my_video.mp4`
를 실행하세요 (위 "실행" 절 참고).

- **프로바이더 독립적**: `EditPlanner`는 `ai.base_provider.AIProvider` 인터페이스에만
  의존합니다. Gemini는 첫 구현체일 뿐이며, `ProviderFactory`에 등록된 다른 프로바이더로
  교체해도 `EditPlanner`/`ContextBuilder`/`PromptBuilder`/`ResponseParser`는 한 줄도
  바뀌지 않습니다.
- **"프로페셔널 유튜브 편집자" 페르소나**: 단순히 "재미있는 부분을 찾아줘"가 아니라,
  빠른 템포 · 자연스러운 대화 흐름 · 침묵/반복/지루한 대기 제거 · 리액션과 중요한
  게임플레이·재미있는 순간 유지 · 단어 중간에서 자르지 않기 · 어색한 전환 피하기를
  명시적으로 지시합니다 (`ai/prompt_templates.py`).
- **토큰 절약**: `ContextBuilder`는 프레임 이미지를 전혀 보내지 않고(텍스트 전용 결정
  단계이므로), 타임스탬프를 소수점 둘째 자리로 반올림해 트랜스크립트만 압축 전달합니다.
- **엄격한 JSON 검증**: AI는 오직 JSON만 응답해야 하며, `ResponseParser`는 마크다운
  코드펜스 등 사소한 포장은 허용하되, 스키마를 만족하지 않는 응답(필수 필드 누락, 잘못된
  `action`, `end <= start` 등)은 예외(`ResponseParsingError`)로 명확히 거부합니다.
  커버리지 누락/겹침처럼 치명적이지 않은 문제는 `EditPlan.warnings`에 기록됩니다.
- **EditPlan 필드**: target length, keep/remove segments, confidence, reasons,
  warnings, processing time, model used — 전부 `to_dict()`/`to_json()`으로 직렬화
  가능합니다.
- **로깅**: 프롬프트 생성, 컨텍스트 크기, 사용된 프로바이더/모델, 응답 시간, 파싱 결과,
  오류까지 각 단계에서 로그를 남깁니다.

## Version 3.5: EditPlan 품질/안전성 강화

AI 결정 시스템 자체만 개선했습니다 — **영상 편집, DaVinci Resolve 연동, 자막 생성,
렌더링은 여전히 하지 않습니다.** Version 3의 흐름 뒤에 검증 단계가 하나 더 붙습니다:

```
... → ResponseParser → EditPlanValidator → EditPlan
```

`EditPlanner.create_edit_plan()`의 기존 2-인자 호출(`analysis`, `target_length_seconds`)은
그대로 동작합니다 — 아래 기능들은 전부 선택적(옵션) 파라미터거나 자동으로 적용되는
후처리 단계입니다.

- **EditPlan Validator** (`ai/edit_plan_validator.py`): 겹치는 구간, 중복 구간, 잘못된
  timestamp, `end <= start`, 지나치게 짧은 클립, 타임라인 공백, 잘못된 action, 영상
  길이를 벗어난 구간을 자동으로 감지합니다. 가능한 경우 자동으로 복구하고(예: 겹침은
  잘라내고, 공백은 `remove`로 채우고, 초단편 클립은 이웃 구간에 병합) 그 내용을
  `EditPlan.warnings`에 남기며, 복구가 불가능하면(예: 남은 구간이 하나도 없음)
  `EditPlanValidationError`로 명확히 거부합니다.
- **Edit Simulator** (`ai/edit_simulator.py`): 영상을 전혀 건드리지 않고 Original Length,
  Estimated Final Length, Removed Percentage, Number of Cuts, Average/Longest/Shortest
  Clip Length, AI Confidence, Validation Warnings를 계산해 `EditSimulationResult`로
  돌려줍니다. `EditPlanner.simulate(plan, analysis)`로 언제든 직접 호출할 수도 있고,
  `create_edit_plan()` 내부에서도 진단 목적으로 자동 실행되어 로그를 남깁니다.
- **Editing Rules Engine** (`ai/editing_rules.py`): remove(Silence, Waiting, Loading
  Screens, Long Walking, Repeated Sentences, Dead Air)와 keep(Reactions, Laughter,
  Funny Moments, Important Gameplay, Story Progress, Boss Fights, Important
  Conversations) 규칙이 더 이상 프롬프트에 하드코딩되어 있지 않습니다. `EditingRules`로
  규칙을 추가/삭제/활성화/비활성화할 수 있고, `PromptBuilder`가 활성화된 규칙만 자동으로
  프롬프트에 렌더링합니다.
- **Scoring System** (`ai/editing_scorer.py`): 각 규칙은 점수를 가집니다(예: Funny +5,
  Reaction +4, Story +5, Gameplay +3, Walking -3, Waiting -5, Silence -10). 이 점수는
  프롬프트에 그대로 노출되어 AI가 EditPlan을 만들 때 참고하며, `EditingScorer`는 AI가
  실제로 만든 각 구간의 `reason`에서 어떤 규칙이 반영됐는지 사후적으로 점수화해
  로그/진단용으로 남깁니다.
- **AI Provider Improvements** (`ai/provider_factory.py`, `ai/base_provider.py`): Gemini는
  계속 완전히 동작하며, `ProviderFactory.register_planned()`로 OpenRouter·Claude·Groq·
  로컬 AI(Ollama/LM Studio)·OpenAI 호환 API를 "아직 구현되지 않았지만 알고 있는 프로바이더"로
  등록해둘 수 있습니다(`app.py`에서 이미 5개 전부 등록됨). `AIProvider`에는
  `requires_api_key`/`requires_base_url` 메타데이터가 추가되어, 나중에 Settings에서
  프로바이더를 고를 때 필요한 입력 필드를 자동으로 결정할 수 있습니다. `EditPlanner`는
  여전히 `AIProvider` 인터페이스에만 의존하므로 새 프로바이더를 실제로 구현해 등록해도
  `EditPlanner`는 한 줄도 바뀌지 않습니다.
- **Prompt Versioning** (`ai/prompt_templates.py`): `create_edit_plan(..., style="gaming")`
  처럼 콘텐츠 스타일을 선택할 수 있습니다 — `general`(기본), `gaming`, `vlog`, `tutorial`,
  `podcast`, `reaction`, `short_form` 중에서 고를 수 있으며, 잘못된 값은 자동으로
  `general`로 대체됩니다.
- **로깅**: Validation(복구 내역), Simulation(길이/컷 통계), Score Calculation(규칙별 점수
  합산), Rule Selection(활성화된 규칙 집합), Provider Used, Prompt Template까지 각 단계가
  스스로 로그를 남깁니다.

## Version 4: UX, 스트리밍, 캐시, 진단 강화

Version 3/3.5의 결정 로직(프롬프트/검증/시뮬레이션/규칙)은 그대로 두고, **사용 경험과
운영 가시성**만 강화했습니다. 영상 편집·렌더링·DaVinci Resolve 연동은 여전히 하지
않습니다.

- **실시간 스트리밍 + AI 진행률 창**: `EditPlanner.create_edit_plan_streaming()`이
  프로바이더 응답을 실시간으로 받으며 Loading Video → Extracting Audio → Running
  Whisper → Extracting Frames → Preparing Prompt → Generating Edit Plan → Parsing
  JSON → Validation → Simulation → Completed 각 단계를 `ProgressEvent`로 방출합니다.
  **AI Editor** 페이지의 `ui/widgets/progress_dialog.py`가 이를 진행률 바 + 경과
  시간 + 단계별 체크리스트로 보여줍니다.
- **취소(Cancel)**: `core/cancellation.py`의 `CancellationToken`을 파이프라인 단계
  경계와 스트리밍 청크마다 확인합니다. 스트리밍 도중 취소하면 모든 프로바이더가
  키 로테이션/재시도 없이 즉시 `OperationCancelledError`를 전파하도록 수정되었고,
  진행 중이던 파이프라인 단계는 항상 끝까지 완료된 뒤에만 취소가 반영되어 프로젝트
  상태가 손상되지 않습니다.
- **프롬프트 최적화**: `ContextBuilder`가 압축 JSON 키(`s`/`e`/`t`)와 인접한 미세
  간격(≤0.05초) 세그먼트 병합을 기본으로 사용하며, `PromptBuilder`가 매 요청마다
  글자 수·추정 토큰 수·(v3 방식 대비) 절감률을 로그로 남깁니다. 대사가 많은 긴 영상은
  트랜스크립트 자체가 크기를 지배하므로 항상 2,000 토큰 밑으로 내려가지는 않지만,
  프롬프트 오버헤드는 측정 가능한 만큼 줄었습니다 — 자세한 수치는 최종 리포트를
  참고하세요.
- **EditPlan 캐시** (`services/edit_plan_cache_service.py`): 동일한 영상 파일(경로+
  크기+수정 시각)과 동일한 Whisper 백엔드/모델·프레임 간격·프롬프트 스타일·목표
  길이 조합이면 Whisper와 AI 요청을 모두 건너뛰고 캐시된 `AnalysisResult`/`EditPlan`을
  즉시 재사용합니다. 영상 파일이 바뀌거나 설정이 바뀌면 자동으로 무효화됩니다.
- **EditPlan 저장/불러오기** (`services/edit_plan_io_service.py`): `*.editplan.json`
  으로 저장하고, 불러올 때는 Whisper/AI 요청 없이 (표시용으로) 영상 메타데이터만
  가볍게 다시 읽습니다.
- **사람이 읽기 쉬운 EditPlan 뷰어**: AI Editor 페이지가 원시 JSON 대신 타임라인
  형태로 KEEP/REMOVE 구간, 사유, 전체 신뢰도, 경고, 요약을 색상으로 구분해 보여줍니다.
  (참고: 현재 EditPlan 스키마는 세그먼트별이 아닌 계획 전체 단위의 신뢰도만 제공합니다.)
- **AI 요청 로그 + 토큰 사용량** (`services/ai_request_log_service.py`,
  `services/token_usage_service.py`): 프로바이더·모델·지연 시간·스트리밍 시간·입력/
  출력 토큰(추정)·예상 비용·재시도 횟수·finish reason을 **Logs** 페이지의 새
  "AI Requests" 탭에서 확인하고 개별/전체 복사할 수 있습니다.
- **프롬프트 디버그 창**: 실제로 전송된 전체 프롬프트(+ 문자/토큰 수, 절감률)를
  보고, 클립보드 복사 또는 파일 저장할 수 있습니다.
- **벤치마크**: 파이프라인/캐시 조회/AI 생성 각 단계의 소요 시간을 측정해 AI Editor
  페이지 하단에 표시합니다.
- **내보내기**: EditPlan을 JSON/TXT/Markdown 리포트로 내보낼 수 있으며, 요약·사유·
  신뢰도·경고·통계(원본/최종 길이, 컷 수, 평균 클립 길이 등)를 포함합니다.
- **진단 확장**: Runtime Diagnostics에 Whisper Model 행과, "Test Provider
  Connection" 버튼으로 실제 프로바이더에 최소 요청을 보내 지연 시간/성공 여부를
  보여주는 Connection Status 행이 추가되었습니다.
- **Max Retries 설정 노출**: Settings의 각 프로바이더 카드에 "Max retries" 입력란이
  추가되어, 키 로테이션 전에 같은 키로 몇 번 재시도할지 UI에서 직접 조절할 수
  있습니다 (이전에는 설정 파일에만 존재).

## Version 5: DaVinci Resolve 연동 (Studio API + Resolve Free XML Export)

AI Editor에서 EditPlan을 만든 뒤, DaVinci Resolve로 실제 컷 편집 타임라인을 가져오는
두 가지 경로를 제공합니다. 둘 다 원본 미디어를 재인코딩하지 않고, 원본 타임라인/파일은
건드리지 않습니다.

### DaVinci Resolve Studio 사용자

1. Resolve Studio를 실행하고, 원본 영상이 이미 임포트된 프로젝트/타임라인을 엽니다.
2. AI Editor에서 EditPlan 생성(또는 불러오기) 후 **"Apply to Resolve..."** 클릭.
3. Dry Run 미리보기를 확인하고 **"Apply to Resolve..."**로 확정하면, 현재 타임라인을
   복제한 `AutoCutAI_Edit` 타임라인에 REMOVE 구간이 실제로 잘려 반영됩니다. 원본
   타임라인은 변경되지 않습니다.

### DaVinci Resolve Free 사용자 (Resolve 스크립팅 API 불필요)

Resolve Free는 외부 스크립팅 API를 지원하지 않으므로, AutoCutAI는 대신 Resolve가
가져올 수 있는 XML(Final Cut Pro XML 계열) 파일을 생성합니다.

1. AI Editor에서 EditPlan 생성(또는 불러오기).
2. **"Export Resolve XML..."** 클릭 → 미리보기(KEEP/REMOVE 구간, COMPRESS 안내)를
   확인.
3. **"Export XML"**로 저장 위치를 선택합니다. 기본 파일명은
   `<원본파일명>_AutoCutAI.xml`입니다.
4. DaVinci Resolve 실행 → **File > Import > Timeline...**으로 저장한 XML을 임포트.
5. REMOVE 구간이 제거된 상태로 KEEP 구간만 이어붙인 새 Timeline이 생성됩니다.

이 XML Export 기능은 Resolve가 설치되어 있지 않아도 동작합니다(EditPlan과 원본
영상 경로/FPS만 있으면 됨). 영상은 재인코딩되지 않으며, XML은 원본 미디어 파일을
그대로 참조합니다. COMPRESS로 표시된 구간은 이번 버전에서도 배속이 적용되지 않고
정상 속도로 유지됩니다(Preview에 안내 문구 표시).

#### 오디오 버그 수정 (첫 클립만 소리가 나고 이후 클립은 무음이던 문제)

과거 버전에서 생성된 XML은 (1) `<file>`의 `<media><audio/>`가 실제 오디오 정보 없이
비어 있었고, (2) 같은 KEEP 구간의 video clipitem과 audio clipitem 사이에 `<link>`
관계가 없었으며, (3) audio clipitem에 `<sourcetrack>`이 없어, Resolve가 두 번째
KEEP 클립부터는 어떤 오디오를 재생해야 할지 알 수 없었습니다. 그 결과 **첫 번째
KEEP 클립만 오디오가 정상 재생되고, 이후 클립은 영상만 재생되고 오디오는 무음**
이었습니다.

지금은 각 KEEP 구간마다:
- video clipitem(`clipitem-vN`)과 audio clipitem(`clipitem-aN`)이 동일한
  `<start>/<end>/<in>/<out>`으로 생성되고,
- 두 clipitem 모두에 서로를 참조하는 `<link>` 쌍이 들어가 하나의 A/V 클립으로
  묶이며,
- audio clipitem에는 `<sourcetrack><mediatype>audio</mediatype>
  <trackindex>1</trackindex></sourcetrack>`이 포함되고,
- 공유되는 `<file>`의 `<media><audio>`에도 실제 `<samplecharacteristics>`
  (`<samplerate>`/`<depth>`)와 `<channelcount>`가 채워집니다.

자동 검증: `python3 tests/test_v5_xml_audio_fix_manual.py` — 버그 리포트에 나온
그대로(60fps, KEEP 구간 `0-480`/`1050-2760`/`3270-3720`/`6180-8220` 소스 프레임)를
재현해, video/audio clipitem 개수 일치, 모든 쌍의 In/Out 일치, `<link>`/
`<sourcetrack>`/`<file>` 오디오 정보 존재 여부를 자동으로 검사합니다. 기존
`python3 tests/test_v5_xml_export_manual.py`도 함께 통과하는지 확인했습니다(기존
Video export 로직·EditPlan KEEP 계산 로직은 변경하지 않았습니다).

**DaVinci Resolve 20 Free에서 직접 Import 검증하는 방법** (자동 테스트로는 XML
*구조*만 검증되고, 실제 재생 여부는 Resolve에서 직접 확인이 필요합니다):

1. 위 테스트로 생성되는 XML(또는 실제 EditPlan으로 **"Export Resolve XML..."**
   실행 결과)을 원본 `.mp4`와 같은 접근 가능한 경로에 둡니다.
2. Resolve 20 Free 실행 → **File > Import > Timeline...** → 저장한 `.xml` 선택.
3. 타임라인 트랙 헤더에서 A1 트랙의 파형(Waveform) 표시를 켜서, 클립마다 오디오
   파형이 실제로 보이는지 한눈에 확인합니다.
4. 첫 번째 클립뿐 아니라 **두 번째 클립(소스 In=1050/Out=2760, 버그 리포트에서
   지적된 구간)** 을 포함해 모든 KEEP 클립을 재생하며 영상과 오디오가 함께
   나오는지 확인합니다.
5. 필요하면 A1 트랙을 솔로(Solo)로 두고 재생해, 클립별 볼륨/음소거 설정 차이가
   아니라 실제 오디오 데이터 문제였는지 함께 확인합니다.

(참고: 이 저장소 환경에는 Resolve GUI가 없어 위 1~5단계의 실제 Import는 직접
실행해 보지 못했으며, XML 구조 자체만 자동 테스트로 검증했습니다.)

#### 컷 순서 재배치 (브이로그/다큐처럼 여러 장면을 하나의 긴 영상으로 합친 뒤, 컷 순서를 다시 배열하고 싶을 때)

기본적으로 컷(잘라낸 KEEP 구간)들은 항상 **원본 영상의 시간 순서 그대로** 타임라인에
배치됩니다. 이제 `EditPlan.output_order`에 원하는 최종 재생 순서를 지정하면, 같은
KEEP 구간들을 다른 순서로 재배치해서 내보낼 수 있습니다.

- **Chat about EditPlan**에서 "이 장면을 맨 앞으로 옮겨줘", "두 장면 순서를 바꿔줘"
  처럼 요청하면, AI가 응답 JSON에 `"reorder": [{"start":.., "end":..}, ...]` 필드로
  새 순서를 제시합니다. 이건 반드시 **현재 KEEP/COMPRESS 구간 전체의 순열**이어야
  하며(구간을 새로 추가하거나 빼는 게 아니라 순서만 바꾸는 것), 어긋나면
  (`davinci/edit_plan_applier.py`의 `_resolve_keep_order`가) 요청을 거부하고 경고와
  함께 원래 시간 순서로 안전하게 되돌립니다 — 영상이 사라지거나 중복되는 일은
  없습니다.
- 이렇게 정해진 순서는 `services/resolve_export_service.py` → Resolve XML export
  경로에 그대로 반영되어, Import Timeline으로 불러온 결과물이 요청한 순서대로
  배열됩니다. (Resolve Studio 스크립팅 API로 직접 컷하는 `davinci/timeline_editor.py`
  경로는 삭제만 하는 방식이라 순서 재배치를 지원하지 않습니다 — 순서를 바꾸려면
  XML export → Resolve Free Import Timeline 경로를 쓰세요.)
- 자동 검증: `python3 tests/test_reorder_feature_manual.py` — 정상 재배치, 잘못된
  재배치 요청의 안전한 fallback, XML 내보내기가 실제로 순서를 반영하는지, Chat about
  EditPlan에서 받은 재배치가 끝까지 이어지는지(그리고 이후 턴에서 내용만 바뀌어도
  순서가 유지되는지)까지 전부 검사합니다.

## Version 5.3: 폴더 일괄 임포트/분석 + 다큐멘터리 자동 배열 + 예능 편집 프로필

기존에는 영상을 한 번에 하나씩만 선택해서 분석할 수 있었습니다. Version 5.3은 **AI
Editor** 페이지에 "Select Folder... (Batch)" 버튼을 추가해, 폴더 하나를 통째로 넘기면
그 안의 모든 지원 영상(mp4/mov/mkv/avi)을 순서대로 임포트 → 분석(오디오 추출 →
Whisper 전사 → 프레임/모션 추출)까지 자동으로 돌립니다.

- **폴더 일괄 임포트**: `video/folder_importer.py`의 `FolderImporter`가 폴더를 스캔해
  지원 확장자만 골라 임포트합니다. 파일 하나가 손상되어 있어도(코덱 문제, 빈 파일 등)
  나머지 파일은 그대로 진행되며, 실패한 파일은 이유와 함께 건너뛴 목록에 기록됩니다 —
  50개 중 1개가 깨졌다고 나머지 49개까지 막히는 일은 없습니다.
- **일괄 분석**: `video/analysis_pipeline.py`에 추가된 `AnalysisPipeline.run_batch()`가
  기존 Version 2 파이프라인(Video → Audio → Whisper → Frames)을 파일마다 반복 실행하고,
  "3/12번째 파일: interview_04.mp4 — Running Whisper" 같은 진행 상황을 콜백으로
  전달합니다. `services/folder_analysis_service.py`의 `FolderAnalysisService`가 임포트
  → 일괄 분석 → 콘텐츠 유형 분류 → (다큐멘터리인 경우) 자동 배열까지 한 번의 호출로
  오케스트레이션합니다.
- **콘텐츠 유형 자동 판별 (다큐멘터리 vs 예능)**: `ai/content_classifier.py`가 각 클립의
  대사 길이/빈도, 리액션(웃음/느낌표) 비율, 장면전환 빈도, 평균 모션값 같은 이미 계산되어
  있는 신호만으로 "다큐멘터리형(길고 차분한 내레이션, 적은 컷)"인지 "예능형(짧고 잦은
  리액션, 잦은 화면 전환)"인지 휴리스틱으로 추정합니다. 비전-언어 모델 호출 없이 동작하는
  가벼운 규칙 기반 추정이며, 자동 추천일 뿐 언제든 수동으로 다른 프로필을 선택할 수
  있습니다.
- **다큐멘터리 자동 배열**: 폴더의 다수 클립이 다큐멘터리로 판별되면, 각 클립에 내장된
  소스 타임코드(카메라/캡처보드가 파일에 기록한 실제 촬영 시각) → 없으면 파일 수정
  시각 → 그마저 없으면 파일명 순으로 정렬해 대략적인 시간 순서(가편집용 배열)를
  제안합니다. 실제로 자르거나 합치지는 않으며, 어디까지나 "이 순서로 조립하면 될 것
  같다"는 제안입니다.
- **예능/버라이어티 편집 프로필**: `ai/game_profiles.py`에 `documentary`/`variety`
  프로필이 새로 추가되었습니다. `variety` 프로필은 리액션·펀치라인·케미 있는 티키타카는
  KEEP, 어색한 침묵이나 룰 재설명 반복은 REMOVE로 채점해, 예능 특유의 편집 리듬에 맞춘
  EditPlan을 생성하도록 유도합니다. `documentary` 프로필은 반대로 핵심 내레이션·증언·
  반전 순간은 KEEP, 반복 테이크나 장비 세팅 소음은 REMOVE합니다.
- **결과 화면**: 분석이 끝나면 `ui/widgets/folder_results_dialog.py`의
  `FolderResultsDialog`가 감지된 콘텐츠 유형, 추천 편집 프로필, (다큐멘터리라면) 제안된
  순서를 클립별로 보여줍니다. "Use this clip"을 누르면 그 클립이 기존 단일 영상 EditPlan
  생성 플로우에 그대로 로드되고, 폴더 전체에서 추천된 프로필이 자동으로 적용됩니다.
- **분석 캐시 재사용**: 폴더 일괄 분석 중 계산된 Whisper 전사/프레임 데이터는
  `EditPlanCacheService`의 분석 캐시에도 함께 저장되므로, 이후 "Generate Edit Plan"을
  눌러도 같은 Whisper 작업을 다시 하지 않습니다.

### Version 5.3.1: 파일명 기반 멀티캠 그룹핑

예능/버라이어티처럼 여러 대의 카메라(거치캠, POV캠 등)로 동시에 찍는 촬영을 위한
기능입니다. 다음 규칙으로 파일명을 지으면 자동으로 인식됩니다:

    <날짜><시간><카메라 태그>.<확장자>
    예: 20260914_153000_거치1cam.mp4
        20260914_153002_pov1cam.mp4
        2026-09-14_15-30-05_pov2cam.mp4   (대시 구분도 지원)

- `video/multicam_grouper.py`가 파일명에서 날짜+시간을 정규식으로 추출하고, 남는 부분을
  카메라 태그로 사용합니다. 여러 카메라의 촬영 시작 시각이 정확히 똑같을 필요는 없습니다
  — 기본 5초 이내 차이(사람이 손으로 카메라 여러 대를 순서대로 누르는 정도의 오차)는 같은
  촬영분(Take)으로 묶입니다.
- 파일명이 이 규칙과 맞지 않아도 에러가 나지 않습니다 — 그냥 매칭되는 앵글이 없는 단독
  클립으로 처리됩니다.
- `FolderAnalysisResult.multicam_groups`에 감지된 Take들이 담기고, 각 클립의
  `FolderClipEntry.camera_tag`/`multicam_group_index`로 어떤 Take·어떤 카메라인지 알 수
  있습니다. 클립 정렬(`suggested_order`)도 이제 Take의 촬영 시각 → Take 내에서는 카메라
  태그 알파벳 순으로 계산되어, 같은 순간을 찍은 여러 앵글이 항상 나란히 배치됩니다.
- 결과 다이얼로그(`ui/widgets/folder_results_dialog.py`)에 "멀티캠 Take #N — 캠:
  pov1cam" 식으로 표시됩니다.
- **현재 범위**: 이 단계는 "어떤 클립들이 같은 순간을 찍었는지 인식하고 순서를
  맞추는" 것까지입니다. 여러 앵글 중 어느 캠을 어느 구간에 쓸지 AI가 자동으로 골라
  잘라주는 것(실제 멀티캠 편집/앵글 스위칭)은 아직 없습니다 — EditPlan/`ai/edit_planner.py`가
  현재 한 번에 영상 하나만 다루는 구조라, 여러 앵글을 동시에 보고 컷을 결정하게
  하려면 EditPlan 데이터 모델과 프롬프트 구조 자체를 확장해야 하는 별도 작업입니다.
  지금은 감지된 Take 중 원하는 캠 파일을 골라 기존 단일 영상 EditPlan 생성 플로우로
  넘기는 방식으로 씁니다.

## Version 5.4: 멀티캠 자동 앵글 전환 편집

Version 5.3.1이 "어떤 클립들이 같은 촬영분인지"만 알아냈다면, 5.4는 그 다음 단계 --
**어느 순간에 어느 카메라를 보여줄지 자동으로 결정**합니다. 폴더 분석 결과 창에서
감지된 멀티캠 촬영분마다 "자동 편집 생성 (XML)" 버튼이 생기고, 누르면 카메라 전환이
반영된 실제 컷 시퀀스를 DaVinci Resolve Import용 XML로 내보냅니다.

- **결정 엔진** (`ai/multicam_angle_planner.py`): AI 프로바이더(LLM) 호출 없이, 이미
  계산되어 있는 신호만으로 동작하는 휴리스틱입니다.
  - **발화 커버리지**: 그 구간에 대사가 잡혀 있는 카메라를 우선.
  - **리액션 마커**: 자막에 느낌표/웃음(ㅋㅋ 등)이 있으면 그 카메라로 강하게 전환.
  - **모션값**: 가볍게 가중치를 주는 보조 신호.
  - **기본 카메라 보너스**: 파일명에 "거치"/"fixed"/"wide"/"main"이 들어간 카메라는
    아무 일도 없을 때 돌아가는 기본(와이드) 샷으로 약간의 가산점을 받습니다.
  - **히스테리시스**: 최소 유지 시간(기본 3초) 이내에는 다른 카메라 점수가 더 높아도
    전환하지 않아 컷이 정신없이 깜빡이지 않습니다. 단, 현재 카메라의 분량 자체가
    끝나버리면 히스테리시스 무시하고 즉시 다른 카메라로 강제 전환합니다.
- **결과물** (`models/angle_switch_plan.py`): 촬영분 전체를 빈틈없이 덮는
  `AngleSegment` 목록 (몇 초~몇 초 구간에 어떤 카메라, 왜 전환했는지 이유 포함).
- **다중 소스 XML 내보내기** (`davinci/multicam_xml_exporter.py`): 기존
  `resolve_xml_exporter.py`는 소스 파일이 하나라고 가정하지만, 이건 그 자매 모듈로
  클립마다 다른 카메라 파일(`<file>`)을 참조할 수 있습니다. 같은 파일은 한 번만
  정의하고 재사용하며, 타임라인 In/Out과 각 카메라 파일 자체의 Source In/Out을 정확히
  분리해서 계산합니다 (촬영분 상대 시간에서 카메라별 시작 오프셋을 빼서 원본 파일
  기준 시간으로 환산).
- **전제 조건**: 모든 카메라가 같은 프레임레이트/해상도라고 가정합니다 (실제 멀티캠
  촬영 대부분이 그렇습니다). 프레임레이트가 다른 카메라를 섞어 썼다면 먼저 맞춰주는
  것을 권장합니다.
- **검증**: 실제 스크립트로 (a) 리액션 구간에서만 POV캠으로 전환되는지, (b) 카메라
  분량이 먼저 끝나면 강제 전환되는지, (c) 만들어진 XML이 실제로 파싱 가능하고
  타임라인/소스 In-Out이 정확히 일치하는지까지 확인했습니다.

## Version 5.5: COMPRESS 배속이 실제로 XML에 반영됨 (버그 수정)

기존까지는 AI가 EditPlan에서 특정 구간을 COMPRESS(배속)로 판단해도, Resolve XML로
내보낼 때는 그냥 정상 속도로 유지되고 있었습니다 (`davinci/edit_plan_applier.py`가
COMPRESS 구간을 KEEP 구간과 구분 없이 합쳐버렸기 때문). 이번에 이 부분을 고쳤습니다.

- `davinci/edit_plan_applier.py`에 `PlaybackSegment`(구간 + 배속) 개념을 추가하고,
  KEEP-보수(REMOVE의 여집합) 구간을 COMPRESS 경계에서 다시 잘라 배속 정보를 실은
  `ResolveEditPlan.playback_order`를 새로 계산합니다. 기존 `keep_ranges`/`keep_order`는
  그대로 두어(배속 무시, 예전과 100% 동일) 하위 호환성을 유지했습니다.
- `davinci/resolve_xml_exporter.py`가 이제 `playback_order`를 사용해서, 배속 구간은
  "타임라인에서는 더 짧게, 소스에서는 원래 길이 그대로"로 인코딩합니다 — 이게 실제로
  Resolve가 배속으로 읽어들이게 만드는 표준 XMEML 방식입니다. 추가로 "Time Remap"
  필터 블록도 넣어서 Resolve UI에 배속 퍼센트가 라벨로 보이게 했습니다.
- **적용 범위**: XML 내보내기(Export XML)에만 적용됩니다. Resolve Studio 스크립팅으로
  직접 타임라인에 적용하는 "Apply to Resolve" 경로는 아직 배속을 지원하지 않고,
  COMPRESS 구간이 있으면 그렇다고 경고 메시지에 명시됩니다.
- 기존 테스트 4개 스위트(43+36+16+54 케이스) 전부 회귀 없이 통과했고, COMPRESS 관련
  테스트는 새 동작(실제 배속 적용)에 맞게 다시 작성했습니다.

## Version 5.6: 영상 분위기 + 배경음악 스타일 자동 추천

EditPlan을 생성할 때 추가 API 호출 없이, 같은 응답 안에서 AI가 영상 전체의 분위기와
어울리는 배경음악 스타일도 함께 제안합니다.

- `ai/prompt_templates.py`의 출력 스키마에 `mood_recommendation` 객체를 추가했습니다:
  전체 분위기(`overall_mood`), 어울리는 음악 장르 2~4개(`music_genre_suggestions`,
  "Lo-fi hip hop", "Acoustic guitar" 처럼 실제로 무료 음원 사이트에서 검색할 수 있는
  구체적인 이름), 템포 설명(`tempo_description`), 추천 이유(`reasoning`).
- `ai/response_parser.py`는 이 필드를 관대하게 파싱합니다 — 없어도(구버전 프롬프트/
  캐시된 플랜) 에러 없이 `None`, 형식이 이상해도 경고만 남기고 나머지 EditPlan은
  정상 처리됩니다.
- `models/edit_plan.py`의 `MoodRecommendation`이 `EditPlan.to_dict`/`from_dict`에 포함되어
  저장(Save EditPlan)/캐시/불러오기 전 구간에서 그대로 유지됩니다.
- AI Editor 결과 화면에 분위기/템포/BGM 스타일 카드가 표시되고, TXT/Markdown
  내보내기(`services/export_service.py`)에도 같은 내용이 포함됩니다.

## 아키텍처 원칙

- **클래스 기반, 전역 함수 없음**: 모든 로직은 명확한 책임을 가진 클래스 안에 있습니다.
- **타입 힌트 전면 적용**: 모든 공개 함수/메서드에 타입 힌트가 있습니다.
- **의존성 주입**: `app.py`가 유일한 컴포지션 루트이며, 다른 모든 모듈은 생성자를 통해
  협력 객체를 전달받습니다(전역 싱글턴 없음).
- **확장 용이성**: 새 AI 프로바이더는 `AIProvider`를 구현하고 `ProviderFactory`에
  등록하기만 하면 되고(Open/Closed Principle), 새 GUI 페이지는 `BasePage`를 상속한 뒤
  `ui/main_window.py`의 페이지 레지스트리에 한 줄만 추가하면 됩니다.

## 새 AI 프로바이더 추가하기 (예시)

```python
# ai/my_provider.py
from ai.base_provider import AIProvider

class MyProvider(AIProvider):
    @property
    def name(self) -> str:
        return "MyProvider"
    ...

# app.py의 _build_context() 안에서
provider_factory.register("my_provider", lambda: MyProvider(...))
```

기존 코드는 한 줄도 수정할 필요가 없습니다.

## Version Next (vNext): Android/Mobile 지원 기반

데스크톱 GUI를 모바일로 그대로 옮기지 않고, **서버 API + 모바일 클라이언트 분리 아키텍처**로
확장했습니다.

- 새 모듈 `services/mobile_api_service.py`:
  - 업로드된 영상으로 비동기 작업을 생성/큐잉하고(`POST /api/vnext/jobs`), 상태를 조회합니다.
  - 기존 `EditGenerationService`를 그대로 재사용해 분석/편집안 생성 로직을 유지합니다.
  - 완료 시 EditPlan JSON과 Resolve XML을 함께 생성해 모바일/데스크톱 워크플로우를 맞춥니다.
- 새 실행 스크립트 `scripts/run_mobile_api.py`:
  - FastAPI + Uvicorn 서버를 바로 실행하는 엔트리포인트입니다.
- 모바일/안드로이드 연동용 핵심 엔드포인트:
  - `GET /api/vnext/health`
  - `POST /api/vnext/jobs`
  - `GET /api/vnext/jobs/{job_id}`
  - `POST /api/vnext/jobs/{job_id}/cancel`
  - `GET /api/vnext/jobs/{job_id}/plan`
  - `GET /api/vnext/jobs/{job_id}/resolve-xml`
  - `GET /api/vnext/jobs/{job_id}/sync-bundle`

### 범위 및 역할 분리

- 모바일 클라이언트(안드로이드)는 업로드/상태표시/결과조회 UI에 집중합니다.
- 고비용 영상 분석(Whisper/프레임 추출/AI 편집 판단)은 서버에서 실행합니다.
- 결과 포맷은 데스크톱과 호환(동일 EditPlan JSON + Resolve XML)되므로, 모바일에서 시작한
  작업을 데스크톱/Resolve로 이어서 편집할 수 있습니다.
