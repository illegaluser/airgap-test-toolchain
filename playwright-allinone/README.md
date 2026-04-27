# Zero-Touch QA All-in-One (호스트 하이브리드 — Mac / Windows 11)

Jenkins master + Dify + DB 를 **단일 Docker 이미지**로 묶고, 추론 (Ollama) 과 브라우저 실행 (Playwright agent) 은 **호스트에서** 수행하는 하이브리드 배포본. 컨테이너 내부에는 Ollama 도 Jenkins agent 도 없다.

## 현재 구현 상태

2026-04-27 기준 현재 구현은 아래와 같다.

| 항목 | 현재 상태 |
|------|-----------|
| 베이스 이미지 핀 | Jenkins `2.555.1-lts-jdk21`, Dify API/Web `1.13.3`, Dify Plugin Daemon `0.5.3-local` |
| 컨테이너 기동 결과 | `dscore.ttc.playwright` 1개 컨테이너에 Jenkins, Dify, PostgreSQL, Redis, Qdrant, nginx 통합 |
| 자동 프로비저닝 결과 | Dify 관리자 계정, Ollama plugin/provider, `ZeroTouch QA Brain` publish, Jenkins credential `dify-qa-api-token`, Job `ZeroTouch-QA`, Jenkins Node `mac-ui-tester`/`wsl-ui-tester` 자동 등록 |
| 완료 마커 | `/data/.app_provisioned` 생성 시 재기동부터 provision 생략 |
| 최근 실측 검증 | Jenkins `http://localhost:18080/login` → `200`, Dify `http://localhost:18081/` → `307`. v4.1 실환경 출시 검증 (4 모드): execute 14/14, convert 14/14, chat 6/6, doc 20/20 모두 PASS. Sprint 6 chat 모드 3 회 연속 17/17 PASS, 0 retry, 평균 dur 101s |
| Action DSL 런타임 | 14대 DSL (`navigate`, `click`, `fill`, `press`, `select`, `check`, `hover`, `wait`, `verify`, `upload`, `drag`, `scroll`, `mock_status`, `mock_data`) 실행기 구현 완료. Sprint 5 multi-strategy chain + post-condition. Sprint 6 press heuristic localhost/file:// 제외 |
| 자가 치유 구조 | A 단 (executor multi-strategy chain) + B 단 (Healer mutate). Sprint 6 에서 Healer 의 action mutation 을 whitelisted 의미 등가 전이 4쌍 (`select↔fill`, `check↔click`, `click↔press`, `upload↔click`) 으로 확장 — 그룹간 변경은 거절해 false-PASS 차단. healer 호출 시 strategy_trace 주입 |
| Convert 14대 확장 | `zero_touch_qa/converter.py` 가 `set_input_files`/`drag_to`/`scroll_into_view_if_needed`/`page.route` 를 upload/drag/scroll/mock_* 로 변환. Sprint 4C closure |
| Sprint 6 chat 결정성 | `<think>` 출력 금지 prompt 룰 + max_tokens 4096 → 8192 + OLLAMA_CONTEXT_SIZE 8192 → 12288 + sanitizer leading-token 회복 + atomic 16 항목 default SRS_TEXT. chat 모드가 17/17 결정론적 PASS (Sprint 5 §10.5 의 best-effort 위치 폐기) |
| 로컬 회귀 테스트 | integration 단위 82건 + 9대 native 회귀 30건 = 총 **112건 PASS, flake 0** |
| Jenkins 회귀 단계 | Stage `2.4. pytest 회귀 (Sprint 2/3)` 에서 integration/native pytest 를 분리 실행하고 JUnit XML 보존 |
| Sprint 4 운영 기반 | Jenkins agent preflight, Dify credential/model health probe, 30일 artifact retention, Dify 호출 metric(`llm_calls.jsonl`) 계측, Zero Touch QA Report 운영 지표 섹션, `aggregate_llm_sla()` 자동 집계 |

추가 메모:

- `dify-chatflow.yaml`, `architecture.md`, `zero_touch_qa` 런타임은 v4.1 Action DSL 14대 확장 계약을 반영한다.
- 신규 5종 액션(`upload`, `drag`, `scroll`, `mock_status`, `mock_data`)은 fixture 기반 pytest 로 잠겨 있다.
- mock route 는 `TARGET_URL` / `MOCK_BLOCKED_HOSTS` 기준으로 넓은 운영 host intercept 를 차단하며, 운영 우회는 `MOCK_OVERRIDE=1` 일 때만 허용한다.
- Dify Planner/Healer 호출은 `artifacts/llm_calls.jsonl` 에 latency, timeout, retry, status, answer 길이를 JSON Lines 로 기록하고 빌드 종료 시 `aggregate_llm_sla()` 가 `llm_sla.json` 으로 자동 집계한다.
- `index.html` 리포트는 metric 파일이 있을 때 운영 지표 섹션에 LLM SLA / Planner / Healer / healing / flake / pytest 요약과 원본 metric 링크를 표시한다.
- 리포트 "첨부 문서" 섹션은 `doc`/`convert`/`execute` 모드처럼 실제 파일이 업로드됐을 때만 노출. `chat` 모드는 자연어 SRS 가 입력이라 첨부 섹션이 뜨지 않는다.
- chat 모드 SRS 작성 가이드 (Sprint 6): **각 SRS 항목은 정확히 하나의 액션** (atomic). "X 한 뒤 Y" 같은 compound 항목은 첫 액션만 emit 되어 후속 verify 가 false-fail 가능. 기본 SRS_TEXT 는 16 atomic 항목으로 작성됐다.
- Sprint 5/6 변경 상세는 `PLAN_DSL_ACTION_EXPANSION.md` §10/§11 참조.

로컬 회귀 확인:

```bash
cd playwright-allinone
python3 -m pip install -r test/requirements.txt
python3 -m playwright install chromium
python3 -m pytest test/test_sprint2_runtime.py -q
python3 -m pytest test --ignore=test/native -q
python3 -m pytest test/native -q
```

운영 메모:
- 이 환경에서는 `build.sh` 가 호출하는 `docker buildx build --load` 가 `sending tarball` 단계에서 오래 멈출 수 있다.
- 이 경우 동일 Dockerfile/태그로 plain `docker build --platform linux/<arch> -f Dockerfile -t dscore.ttc.playwright:latest .` 로 우회한 뒤 동일하게 컨테이너를 기동하면 된다.

## 빠른 이해

이 문서는 길지만, 실제 사용 흐름은 단순하다.

1. `build.sh` 로 이미지를 만든다.
2. `--redeploy` 또는 `docker run` 으로 컨테이너를 올린다.
3. 로그에서 `NODE_SECRET` 이 나올 때까지 기다린다.
4. 호스트에서 `mac-agent-setup.sh` 또는 `wsl-agent-setup.sh` 를 실행한다.
5. Jenkins 에서 `ZeroTouch-QA` 를 실행한다.

## 이 문서를 어디서부터 읽으면 되나

| 상황 | 먼저 읽을 섹션 |
|------|----------------|
| 처음 설치부터 전체 흐름을 따라가고 싶음 | §1 |
| 이 이미지가 내부적으로 무엇을 포함하는지 알고 싶음 | §2 |
| 재기동, 초기화, 모델 변경 같은 운영 작업을 찾고 있음 | §3 |
| 문제 해결이 필요함 | §4 |

## 3분 시작

같은 머신에서 빌드와 실행을 한 번에 하려면 아래가 가장 짧은 경로다.

```bash
cd playwright-allinone
chmod +x *.sh
./build.sh --redeploy
```

그 다음 확인 순서:

1. `docker ps | grep dscore.ttc.playwright`
2. `docker logs -f dscore.ttc.playwright`
3. 로그에 `NODE_SECRET:` 과 `프로비저닝 완료` 출력 확인
4. 새 터미널에서 `./mac-agent-setup.sh` 또는 `./wsl-agent-setup.sh`
5. 브라우저에서 Jenkins `http://localhost:18080` 접속

## 지금 자동으로 되는 것

컨테이너 첫 기동 시 아래가 자동으로 완료된다.

- Dify 관리자 계정 생성
- Ollama plugin 설치 및 provider 등록
- `ZeroTouch QA Brain` import/publish
- Dify API Key 발급
- Jenkins credential `dify-qa-api-token` 등록
- Jenkins job `ZeroTouch-QA` 생성
- Jenkins node `mac-ui-tester` 또는 `wsl-ui-tester` 생성
- `/data/.app_provisioned` 생성

즉, 사용자가 직접 챙길 것은 대체로 3가지다.
- 호스트 Ollama 가 살아 있는지
- 호스트 agent 를 붙였는지
- Jenkins 파이프라인 입력값이 맞는지

---

## 이 문서 읽는 법

| 목적 | 섹션 |
|------|------|
| **처음 설치하고 Pipeline 을 돌리기까지** — Mac 또는 Windows 11 | [§1 설치 및 구동 절차](#1-설치-및-구동-절차) |
| "이 스크립트/파일이 정확히 뭐 하는지" 레퍼런스 | [§2 구성 파일 레퍼런스](#2-구성-파일-레퍼런스) |
| 재시작 / 백업 / 업그레이드 / Ollama 모델 변경 등 일상 운영 | [§3 운영 가이드](#3-운영-가이드) |
| 문제가 생겼을 때 | [§4 트러블슈팅](#4-트러블슈팅) |
| 프로세스 토폴로지 / 볼륨 구조 / 포트 | [부록 A](#부록-a-토폴로지--볼륨-구조) |

> **이 폴더 (`playwright-allinone/`) 는 자체 완결이다.** 폴더만 압축해서 다른 머신으로 옮겨도 그대로 빌드 + 구동 가능하다. 모든 경로는 이 폴더를 기준으로 한다.

---

## 핵심 개념 (먼저 읽기)

**전체 시스템은 두 조각으로 나뉜다**:

| 실행 위치 | 무엇이 도는가 | 어떻게 기동하나 |
| --- | --- | --- |
| Docker 컨테이너 (1 개) | Jenkins **master** + Dify + PostgreSQL + Redis + Qdrant + nginx | `docker run ...` (1 회) |
| 호스트 셸 | Jenkins **agent** (Playwright Chromium 포함) + Ollama | `./mac-agent-setup.sh` (Mac) 또는 `./wsl-agent-setup.sh` (Win) |

**왜 agent 와 Ollama 는 컨테이너 밖인가?**

- Playwright Chromium 창을 **데스크탑에 띄우려면** 호스트 디스플레이 (macOS Aqua / WSLg) 접근이 필수 — 컨테이너 headless Linux 에서는 불가
- Ollama 는 Metal (Mac) / CUDA (Win) **GPU 가속이 강력 권장** — CPU-only 도 기술적으로 가능하지만 속도가 크게 떨어져 Sprint 1의 목표 운용 형태는 아님
- 그래서 "GPU 의존 + 디스플레이 의존" 두 컴포넌트를 호스트로 분리. 나머지 백엔드는 컨테이너에.

**사용자가 직접 실행하는 스크립트는 단 2 개**:

1. `build.sh` — 이미지 빌드. 같은 머신에서 바로 배포까지 하려면 `--redeploy` 옵션 추가.
2. `mac-agent-setup.sh` (Mac) / `wsl-agent-setup.sh` (Windows 11 WSL2) — 호스트 Jenkins agent 기동.

> 그 외 파일 (`entrypoint.sh`, `provision.sh`, `pg-init.sh`, `supervisord.conf`, `nginx.conf`, `requirements.txt`, `Dockerfile`) 은 **컨테이너 내부에서 자동 실행되거나 빌드 타임에만 쓰이는 내부 파일**이라 사용자가 직접 건드리지 않는다. 상세는 [§2](#2-구성-파일-레퍼런스) (참조 전용).
>
> `build.sh` 는 최신 이미지 핀을 기본값으로 내장하지만, macOS Docker Desktop 에서는 buildx export 단계가 멈출 수 있어 필요 시 plain `docker build` fallback 을 운영 절차에 포함해야 한다.

---

## 개요

### 아키텍처

```text
┌─ 컨테이너 (dscore.ttc.playwright:latest) ─────────────────┐
│  Jenkins master   (:18080, :50001 JNLP)                   │
│  Dify api/worker/web/plugin-daemon  (:5001, :3000, :5002) │
│  PostgreSQL 15 / Redis / Qdrant                           │
│  nginx (:18081 → Dify)                                    │
│  supervisord (10 개 프로세스)                             │
└────────┬─────────────────────────────────────┬────────────┘
         │                                     │
 host.docker.internal:11434 (Ollama)     JNLP :50001 (agent)
         │                                     │
┌────────┴───────────────────────────┐  ┌──────┴─────────────┐
│ 호스트 Ollama                      │  │ 호스트 agent       │
│  - Mac:  macOS 네이티브 (Metal)    │  │  - Mac:  bash      │
│  - Win:  Windows 네이티브 (CUDA)   │  │  - Win:  WSL2 bash │
└────────────────────────────────────┘  │  JDK 21 + Python + │
                                        │  Playwright        │
                                        │  → headed Chromium │
                                        │    (Mac: 창 직접 / │
                                        │     Win: WSLg 경유)│
                                        └────────────────────┘
```

### 왜 하이브리드인가

Docker Desktop 이 Linux 컨테이너에 **GPU / 디스플레이 전달을 지원하지 않는다**:

- Metal GPU passthrough 부재 (Mac) → 컨테이너 Ollama 1-2 tok/s
- X/Wayland 소켓 없음 → 컨테이너 Playwright headed Chromium 불가
- Windows 는 LLM 은 CUDA 로 컨테이너 가능하지만 headed 창을 Windows 데스크탑에 띄울 수 없음

그래서 **성능·UX 에 민감한 두 컴포넌트 (LLM 추론, 브라우저) 를 호스트로** 빼냈다.

### 트레이드오프

| 항목 | 값 |
|------|-----|
| 이미지 크기 (비압축) | ~10GB |
| 배포 tar.gz | 2-3GB |
| 빌드 | 10-30분 (초기) / 3-5분 (캐시) |
| 첫 기동 | 3-5분 (provision 포함) |
| 이후 기동 | 30-60초 |
| 호스트 RAM | 16GB+ |
| 호스트 디스크 여유 | 20GB+ (빌드 시) |
| LLM | Mac Metal / Windows CUDA 기준 수십 tok/s, CPU-only 는 현저히 느림 |
| 브라우저 | 호스트 네이티브 headed Chromium |
| 외부 포트 | 18080 (Jenkins) / 18081 (Dify) / 50001 (JNLP) |

---

## 1. 설치 및 구동 절차

**이 순서대로만 따라하면 됩니다.** Mac / Windows 11 공통 4 단계.

### 1.0 오프라인 빌드/구동 사전 준비

이 스택도 폐쇄망 반입 시에는 "온라인 준비 머신에서 미리 만들고, 오프라인 머신에서 load/run" 방식으로 가져가는 것이 안전합니다. 특히 Playwright 스택은 컨테이너 이미지만 있으면 끝이 아니라 **호스트 Ollama**, **호스트 agent 실행 환경**, **브라우저 의존성** 까지 함께 준비돼야 합니다.

#### 1.0.1 준비물 목록

| 분류 | 준비물 | 설명 |
|------|--------|------|
| 온라인 준비 머신 | Docker가 동작하는 macOS 또는 Linux 1대 | 이미지 빌드 및 tarball 생성 |
| 오프라인 운영 머신 | macOS 또는 Windows 11 WSL2 1대 | 실제 Jenkins/Dify/agent 실행 대상 |
| 저장매체 | USB / NAS / 외장 SSD (권장 32GB 이상) | 이미지와 설치 파일 전달 |
| 설치 파일 | Docker Desktop installer | 운영 머신 Docker 설치용 |
| 설치 파일 | Ollama installer | 운영 머신 Ollama 설치용 |
| 이미지 산출물 | `dscore.ttc.playwright-*.tar.gz` | 오프라인 `docker load` 용 |
| 레포 폴더 | `playwright-allinone/` 전체 | `build.sh`, agent 스크립트, README 포함 |
| Ollama 모델 | `gemma4:26b` (Sprint 5+ 기본) — 추가로 RAG 트랙은 `bona/bge-m3-korean:latest` (1.2GB) 필요 | Planner/Healer + KB embedding |
| 호스트 런타임 | JDK 21, Python 3.11+ | agent 실행용 |

#### 1.0.2 오프라인 반입 패키지 예시

```text
<반출매체 루트>/
├── playwright-allinone/
│   ├── build.sh
│   ├── mac-agent-setup.sh
│   ├── wsl-agent-setup.sh
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── provision.sh
│   └── README.md
├── dscore.ttc.playwright-<timestamp>.tar.gz
├── ollama-models/
│   └── models/...
└── installers/
    ├── Docker.dmg
    ├── Ollama-darwin.zip
    ├── Docker-Desktop-Installer.exe
    └── OllamaSetup.exe
```

#### 1.0.3 출발 전 체크

오프라인 현장으로 이동하기 전에 아래를 확인하세요.

1. 운영 머신 아키텍처와 빌드한 이미지 아키텍처가 맞다.
2. `dscore.ttc.playwright-*.tar.gz` 파일이 실제로 열리고 크기가 비정상적으로 작지 않다.
3. `gemma4:26b` (그리고 RAG 트랙이면 `bona/bge-m3-korean:latest` 도) 모델이 들어 있는 `ollama-models/` 복사본이 있다.
4. 운영 OS 에 맞는 Docker/Ollama 설치 파일이 있다.
5. agent 를 붙일 사용자 계정에서 JDK 21, Python 3.11+ 설치 또는 설치 권한이 있다.

### 1.1 플랫폼별 사전 준비 (최초 1회)

공통: Docker Desktop 4.30+, RAM 16GB+, 디스크 여유 20GB+, 인터넷 연결 (빌드 시).

#### Mac (Apple Silicon)

```bash
# A. Ollama + 모델
brew install ollama
brew services start ollama
ollama pull gemma4:26b

# B. JDK 21 + Python 3.11+
brew install openjdk@21
brew install python@3.12

# 확인
ollama list | grep gemma4:26b
java -version          # "21.x"
python3 --version      # "3.11" 이상
```

> 위 A/B 를 건너뛰고 **Step 3 에서 `AUTO_INSTALL_DEPS=true`** 로 자동 설치도 가능. brew 는 직접 먼저 설치돼 있어야 함.

#### Windows 11

Windows 측 (PowerShell 관리자):

```powershell
# A. WSL2 + Ubuntu 22.04+ (한 번만)
wsl --install -d Ubuntu-22.04     # 재부팅 1회 필요. 재부팅 후 Ubuntu 앱 기동 → user 설정

# B. NVIDIA 드라이버 최신 (GPU 사용 시) — WSL2 에 CUDA 자동 노출

# C. Ollama — Windows 네이티브 설치
winget install Ollama.Ollama       # 트레이 앱 자동 기동
ollama pull gemma4:26b
```

> **왜 Windows 네이티브 Ollama?** 컨테이너는 Docker Desktop 포워딩으로 `host.docker.internal:11434` → Windows 127.0.0.1 에 도달한다. WSL 안에 Ollama 를 중복 설치하면 모델이 두 번 디스크 차지.

이후 **모든 명령은 WSL2 Ubuntu 셸** 안에서 실행:

```bash
# D. WSL Ubuntu 안 — JDK 21 + Python 3.11+
sudo apt update

# JDK 21 (Ubuntu 22.04 / 24.04 공통)
sudo apt install -y openjdk-21-jdk-headless

# Python 3.11+ — Ubuntu 기본 저장소는 배포판마다 다름:
#   Ubuntu 24.04 (Noble) 기본: python3.12  → `sudo apt install -y python3.12 python3.12-venv`
#   Ubuntu 22.04 (Jammy) 기본: python3.10  → `sudo apt install -y python3.11 python3.11-venv`
# (→ 무엇이 가용한지 모르면 아래 단계 D 를 스킵하고 Step 3 에서 `AUTO_INSTALL_DEPS=true`
#    로 agent-setup 을 돌리면 스크립트가 python3.12 → python3.11 → deadsnakes PPA
#    순서로 자동 설치해 준다)
sudo apt install -y python3.12 python3.12-venv    # 배포판에 맞는 버전으로 대체

# 확인
nvidia-smi             # Windows 드라이버가 WSL2 에 GPU 노출
java -version
python3 --version
```

> **Playwright Chromium 의존 라이브러리** (libasound2 / libgbm1 / libnss3 등) 는 Step 3 의 `wsl-agent-setup.sh` 가 WSL2 를 감지해 `sudo playwright install-deps chromium` 으로 자동 설치한다. WSL 최소 이미지에서도 별도 작업 불필요.

폐쇄망 Windows 오프라인 설치는 [§4.12](#412-windows-wsl2-오프라인-설치-폐쇄망) 참조.

### 1.2 빠른 경로: 같은 머신 (추천)

**빌드와 실행을 같은 머신에서** 한다면, **한 줄 명령이 빌드 → 컨테이너 기동 → 호스트 agent 연결까지 전부 수행**한다.

```bash
git clone <저장소> && cd <저장소>/playwright-allinone

# 최초 1회만 — git 이 exec bit 를 보존하지 않는 환경 대비
chmod +x *.sh

# 같은 머신 one-shot 배포
./build.sh --redeploy
```

이 명령이 하는 일 (내부 순서):

1. 이미지 빌드 (10-30 분 초기 / 3-5 분 캐시 재사용)
2. 기존 `dscore.ttc.playwright` 컨테이너가 있으면 `docker rm -f` 후 새 컨테이너 기동
3. 컨테이너 로그에서 `NODE_SECRET` 출력까지 대기 (최대 15 분)
4. `mac-agent-setup.sh` / `wsl-agent-setup.sh` 를 새 터미널로 띄워 **호스트 agent 연결**

완료 후 <http://localhost:18080> 에서 Jenkins 접속 → [§1.4 Pipeline 실행](#14-step-4-첫-pipeline-실행-검증).

**추가 옵션**:

```bash
./build.sh --redeploy --fresh      # 기존 dscore-data 볼륨까지 삭제 (provision 재수행)
./build.sh --redeploy --no-agent   # 컨테이너만 재기동, agent 는 수동으로
```

> 폐쇄망 / 원격 타겟 (빌드 머신과 실행 머신이 다름) 이라면 `--redeploy` 대신 [§1.3 분리 배포](#13-분리-배포-빌드-머신과-실행-머신이-다른-경우)를 따른다.

> `docker buildx build` 가 `sending tarball` 에서 멈추면 아래 plain build 로 동일 결과를 만들 수 있다.
>
> ```bash
> docker build --platform linux/arm64 -f Dockerfile -t dscore.ttc.playwright:latest .
> # 또는 amd64 머신이면 --platform linux/amd64
> ```

### 1.3 분리 배포: 빌드 머신과 실행 머신이 다른 경우

빌드는 인터넷 되는 머신에서, 실행은 폐쇄망 / 원격 서버에서 해야 하는 경우. **3 단계 수동 실행**.

#### Step 1 (빌드 머신): 이미지 빌드

```bash
git clone <저장소> && cd <저장소>/playwright-allinone
chmod +x *.sh                        # 최초 1회
./build.sh 2>&1 | tee /tmp/build.log
```

- 10-30분 걸림 (초기), 3-5분 (캐시 재사용)
- 출력: `dscore.ttc.playwright-<timestamp>.tar.gz` (2-3GB, 같은 폴더에 저장)
- 플랫폼 자동 감지 (Mac arm64 → linux/arm64, Win/Linux x86 → linux/amd64)
- macOS 에서 buildx 가 export 단계에서 멈추면 plain `docker build` 로 로컬 이미지(`dscore.ttc.playwright:latest`)를 만든 뒤 `docker save` 로 tarball 을 따로 만들면 된다.

산출된 tar.gz 를 USB / 사내망으로 실행 머신에 전달. `playwright-allinone/` 폴더도 함께 전달 (실행 머신에서 `mac-agent-setup.sh` / `wsl-agent-setup.sh` 를 쓰기 위함).

#### Step 2 (실행 머신): 이미지 로드 + 컨테이너 기동

> **이 단계는 Jenkins master + Dify + DB 가 들어있는 컨테이너 하나만 기동한다. Jenkins agent 는 이 컨테이너 안에 **없다** — Step 3 에서 호스트에 별도로 기동한다.** ([핵심 개념](#핵심-개념-먼저-읽기) 참조)

```bash
docker load -i dscore.ttc.playwright-*.tar.gz

docker run -d --name dscore.ttc.playwright \
  -p 18080:18080 -p 18081:18081 -p 50001:50001 \
  -v dscore-data:/data \
  --add-host host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gemma4:26b \
  --restart unless-stopped \
  dscore.ttc.playwright:latest

docker logs -f dscore.ttc.playwright   # NODE_SECRET 확인. 확인되면 Ctrl+C.
```

**첫 기동 (볼륨 `dscore-data` 가 비어있을 때) — 3-5 분 소요**:

| 경과 | 로그 마커 |
| --- | --- |
| 0:00 | `최초 seed: /opt/seed → /data` |
| 0:05-0:30 | `supervisord 기동` + 10 개 프로세스 RUNNING |
| 0:30-1:30 | dify-api HTTP 200 대기 (amd64 는 60-90s 걸릴 수 있음) |
| 1:30-2:30 | `[▶] 1. 서비스 헬스체크` → `2. Dify 초기 설정` |
| 2:30-4:00 | Ollama 플러그인 / credential swap / Chatflow / API Key / Jenkins Job / Node |
| ~3-5분 | **`NODE_SECRET: <64자 hex>`** 로그 출력 → Step 3 에서 사용 |
| 직후 | `[▶] === 프로비저닝 완료 ===` + `/data/.app_provisioned` 생성 |

**이후 기동 (`/data/.app_provisioned` 존재) — 30-60 초**:

- provision 단계 전체 스킵, supervisord 만 재기동
- `NODE_SECRET` 은 동일 값 재출력 (Jenkins Node 상태 유지)

컨테이너 내부 동작의 파일 단위 상세: [§2.4 entrypoint.sh](#24-entrypointsh) + [§2.5 provision.sh](#25-provisionsh).

#### Step 3 (실행 머신): 호스트 Jenkins agent 연결

> **컨테이너에는 Jenkins master 만 있고 agent 는 없다. agent 는 호스트에 별도 프로세스로 기동해야 한다** — 이유는 [§핵심 개념](#핵심-개념-먼저-읽기). 아래 스크립트가 자동으로 `NODE_SECRET` 을 `docker logs` 에서 추출해 JNLP 로 연결한다.

**새 터미널 하나를 agent 전용으로 열어둔다** (foreground 실행이므로 이 터미널을 닫으면 agent 연결 끊김):

```bash
cd <저장소>/playwright-allinone       # 실행 머신에 복사해 둔 폴더

# Mac
./mac-agent-setup.sh

# Windows 11 / WSL2 Ubuntu
./wsl-agent-setup.sh
```

스크립트가 자동으로 수행하는 작업:

1. 기존 `agent.jar` 프로세스 + 중복 setup 인스턴스 정리 (session id 기반)
2. `docker logs` 에서 **NODE_SECRET 자동 추출**
3. JDK 21 / Python 3.11+ 확인
4. venv 생성 + Playwright Chromium 설치 (`~/.dscore.ttc.playwright-agent/venv`)
5. Jenkins Node `mac-ui-tester` 의 remoteFS 를 호스트 절대경로로 갱신
6. agent.jar 다운로드
7. foreground 로 agent 기동 → `INFO: Connected` 출력되면 연결 완료

의존성 자동 설치가 필요하면 ([§1.1](#11-플랫폼별-사전-준비-최초-1회) B/D 단계 스킵) `AUTO_INSTALL_DEPS=true` 를 앞에 붙인다:

```bash
AUTO_INSTALL_DEPS=true ./mac-agent-setup.sh       # brew 로 자동 설치
AUTO_INSTALL_DEPS=true ./wsl-agent-setup.sh       # sudo apt 로 자동 설치
```

스크립트 내부 동작 상세: [§2.2 agent-setup](#22-mac-agent-setupsh--wsl-agent-setupsh).

### 1.4 Step 4: 첫 Pipeline 실행 (검증)

Step 1-3 ([§1.2](#12-빠른-경로-같은-머신-추천) 또는 [§1.3](#13-분리-배포-빌드-머신과-실행-머신이-다른-경우)) 완료 후, 브라우저에서 <http://localhost:18080> → `admin / password` 로 로그인:

**[1] Dashboard → `ZeroTouch-QA` → Build with Parameters**

**[2] 검증된 데모 입력값** (기본값 대신 아래를 권장):

| 파라미터 | 값 |
| --- | --- |
| `RUN_MODE` | `chat` |
| `TARGET_URL` | `https://www.naver.com` |
| `SRS_TEXT` | `검색창에 si qa 입력 후 검색 실행하고 결과 확인` |
| `DOC_FILE` | (비워둠) |
| `HEADLESS` | 체크 여부는 기호대로 — 해제 시 호스트에 Chromium 창 표시 |

**[3] Build → 30-90 초 내 `Finished: SUCCESS`**

> **왜 네이버인가?** Google 은 headless Chromium 을 적극적으로 차단 (captcha 또는 /sorry/ 리다이렉트) 해 검증에 부적합하다. 네이버는 차단이 약하다. 기본값 `https://www.google.com` 은 기동 확인용으로만 제공되며, 신뢰성 있는 검증에는 위 네이버 시나리오가 권장된다.
>
> **LLM 비결정성 안전망**: 내부의 `zero_touch_qa` 는 Dify 응답을 받은 즉시 구조 검증 (9 대 액션 / target 존재) 하고, 실패 시 자동으로 **최대 3 회 재생성** 한다 (`[Retry N/3]` 로그). 재시도 후에도 step 1 이 navigate 가 아니면 Guard 가 `TARGET_URL` 로 자동 prepend. 4B 모델이 가끔 규칙을 놓쳐도 파이프라인이 실패하지 않도록 설계된 [4 계층 방어 스택](#llm-비결정성-방어-스택) 참조.

접속 정보:

| 서비스 | URL | 기본 계정 |
| --- | --- | --- |
| Jenkins | <http://localhost:18080> | admin / password |
| Dify | <http://localhost:18081> | admin@example.com / Admin1234! |

프로비저닝이 정말 끝났는지 확인하는 체크리스트: [§3.5 프로비저닝 체크리스트](#35-프로비저닝-체크리스트).

---

## 2. 구성 파일 레퍼런스

> **이 섹션은 참조 전용이다. 설치 / 구동이 목적이면 [§1](#1-설치-및-구동-절차) 만으로 충분하다.**
> 아래 파일들은 계층이 다르다 — 각 제목 옆 라벨로 구분:
>
> - **[사용자 실행]** — 사용자가 직접 호출하는 스크립트 (2 개)
> - **[컨테이너 내부]** — 컨테이너가 기동하면서 자동 실행하는 내부 스크립트/설정 (사용자 수동 실행 안 함)
> - **[빌드 타임]** — `docker buildx build` 중에만 쓰이는 파일
> - **[참조]** — 이미지 구조/리소스 문서용 (직접 실행 안 함)

각 섹션 포맷은 일관:

- **목적** — 한 줄 요약
- **주요 env / 옵션**
- **동작 요약**
- **수정이 필요할 때**

### 2.1 `build.sh`

> 🧑 **[사용자 실행]** — 온라인 빌드 머신에서 수동 호출.

**목적**: 이미지 `*.tar.gz` 를 산출. 선택적으로 같은 호스트에 바로 재배포까지.

**주요 env / 옵션**:

| 이름 | 기본값 | 비고 |
|------|--------|------|
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | 이미지 repo:tag |
| `TARGET_PLATFORM` | `uname -m` 자동 감지 | Apple Silicon → `linux/arm64`, 그 외 → `linux/amd64`. override 시 qemu silent-fail 주의 |
| `OLLAMA_MODEL` | `gemma4:26b` | Dify provider 등록 시 사용될 모델 id |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<ts>.tar.gz` | 출력 tar.gz 파일명 |
| `FORCE_PLUGIN_DOWNLOAD` | `false` | `true` 면 `jenkins-plugins/` / `dify-plugins/` 에 파일이 있어도 재다운로드 (버전 갱신 시). 기본은 기존 파일 재사용 — **airgap 환경에서 네트워크 없이 빌드 가능**. git 저장소에 플러그인 53 개 + Dify plugin 1 개가 동반된다 |
| `--redeploy` | — | 빌드 후 기존 컨테이너 rm → run → NODE_SECRET 대기 (최대 15분) → agent-setup 자동 기동 |
| `--fresh` | — | `--redeploy` 와 함께 — `dscore-data` 볼륨까지 삭제 (provision 재수행) |
| `--no-agent` | — | `--redeploy` 와 함께 — 컨테이너만 재기동, agent 는 수동으로 |

**동작 요약**:

1. Jenkins 플러그인 `.hpi` 재귀 다운로드 → `jenkins-plugins/` (40-50 개)
2. Dify 플러그인 `.difypkg` 다운로드 → `dify-plugins/`
3. `docker buildx build` → [Dockerfile](#23-dockerfile) 로 이미지 생성
4. `docker save | gzip -1` → `dscore.ttc.playwright-<ts>.tar.gz`
5. (`--redeploy` 시) 컨테이너 재기동 + agent-setup 자동 호출

**수정이 필요할 때**: 플러그인 목록 변경 / 빌드 플로우 추가 / 재배포 옵션 확장.

### 2.2 `mac-agent-setup.sh` / `wsl-agent-setup.sh`

> 🧑 **[사용자 실행]** — 호스트 셸 (Mac Terminal / WSL2 Ubuntu bash). 반복 실행 가능.

**목적**: 호스트에서 Jenkins agent 환경 구성 + `agent.jar` foreground 실행. 두 파일은 동일한 **7 단계** 를 bash 로 수행, 차이는 패키지 매니저 (brew vs apt) 와 JDK 경로 탐색뿐. 매번 이전 agent 를 자동 정리하고 새로 연결.

**주요 env**:

| 이름 | 기본값 | 비고 |
| --- | --- | --- |
| `NODE_SECRET` | (자동 추출) | 없으면 `docker logs $CONTAINER_NAME` 에서 추출 |
| `CONTAINER_NAME` | `dscore.ttc.playwright` | 자동 추출 대상 컨테이너 |
| `AUTO_INSTALL_DEPS` | `false` | `true` 시 brew/apt 로 의존성 자동 설치 |
| `OLLAMA_MODEL` | `gemma4:26b` | 존재 확인 대상 모델 (정보성) |
| `JENKINS_URL` | `http://localhost:18080` | |
| `AGENT_NAME` | `mac-ui-tester` | Pipeline `agent { label ... }` 과 일치해야 함 |
| `MAC_AGENT_WORKDIR` / `WSL_AGENT_WORKDIR` | `$HOME/.dscore.ttc.playwright-agent` | 작업 디렉토리 |
| `FORCE_AGENT_DOWNLOAD` | `false` | `true` 시 agent.jar 강제 재다운로드 |

**동작 요약**:

| 단계 | 내용 |
| --- | --- |
| **0-A. 정리** | 기존 `agent.jar` + 다른 세션의 setup 인스턴스를 session id 기반으로 종료. Jenkins disconnect 인지 대기 (최대 15s) |
| **0-B. NODE_SECRET 추출** | env 없으면 `docker logs` 에서 자동 추출 |
| **1. Ollama 도달성** | 호스트 Ollama 가 reachable 한지 정보성 확인 (실패해도 진행 — 컨테이너 쪽 경로 별도) |
| **2. JDK 21** | 엄격 탐지. Mac: `/opt/homebrew/opt/openjdk@21` 또는 `/usr/libexec/java_home -v21`. WSL2: `/usr/lib/jvm/{temurin,java}-21-*` |
| **3. Python 3.11+** | `sys.version_info >= (3,11)` 만족하는 `python3` |
| **4. venv + Chromium** | `~/.dscore.ttc.playwright-agent/venv` 생성 + `pip install requests playwright pillow` + `playwright install chromium` |
| **5. Node remoteFS** | Groovy 로 Jenkins Node `mac-ui-tester` 의 remoteFS 를 호스트 절대경로로 갱신 + workspace venv symlink |
| **6. agent.jar** | `$JENKINS_URL/jnlpJars/agent.jar` 다운로드 |
| **7. 기동** | `run-agent.sh` 생성 + `exec java -jar agent.jar ...` foreground 실행 → `INFO: Connected` |

**결과물**:

```text
~/.dscore.ttc.playwright-agent/
├── venv/                         # Python 3.11+ + Playwright
├── agent.jar                     # Jenkins remoting jar
├── run-agent.sh                  # 재연결 시 이 스크립트 직접 실행해도 됨
└── workspace/ZeroTouch-QA/
    └── .qa_home/                 # Pipeline Stage 1 이 생성
        ├── venv → /home/user/.dscore.ttc.playwright-agent/venv (symlink)
        └── artifacts/
```

**SCRIPTS_HOME 산정**: `run-agent.sh` 는 `SCRIPTS_HOME=<이 스크립트의 부모 폴더>` (= `playwright-allinone/`) 를 export 한다. Pipeline 의 Stage 3 가 `PYTHONPATH=$SCRIPTS_HOME` 로 `zero_touch_qa` 패키지를 import 한다. **이 폴더를 다른 위치로 옮기면 agent-setup 을 다시 실행해 경로를 갱신해야 한다.**

**수정이 필요할 때**: 새 OS/패키지 매니저 지원, JDK/Python 탐색 경로 추가, Chromium 외 다른 브라우저 추가.

### 2.3 `Dockerfile`

> 📖 **[참조]** — `build.sh` 의 `[3/4]` 단계가 `docker buildx` 로 호출. 사용자가 직접 실행 안 함.

**목적**: 이미지 레이아웃 정의 (2-stage 멀티 빌드).

**동작 요약**:

- **Stage 1** — 공식 이미지에서 `/opt/dify-*` 애플리케이션 디렉토리 복사만
  - `langgenius/dify-api:1.13.3`
  - `langgenius/dify-web:1.13.3`
  - `langgenius/dify-plugin-daemon:0.5.3-local`
- **Stage 2** (final) — `jenkins/jenkins:2.555.1-lts-jdk21` 베이스에 합치기
  - OS 패키지: postgresql-15, redis-server, nginx, python3+venv, supervisor, tini, Playwright 의존 라이브러리
  - Stage 1 산출물 COPY
  - Qdrant 바이너리 + Node.js (TARGETARCH 분기 — amd64 는 glibc / arm64 는 musl)
  - `jenkins-plugins/` + `dify-plugins/` seed
  - 프로비저닝용 스크립트/설정 파일 + 같은 폴더의 [ZeroTouch-QA.jenkinsPipeline](#210-seed-자원-빌드-시점에-이미지로-들어가는-파일들) / [dify-chatflow.yaml](#210-seed-자원-빌드-시점에-이미지로-들어가는-파일들) 을 이미지에 포함
  - 빌드 타임에 `pg-init.sh` 실행 → `/opt/seed/pg/` 에 Dify DB initdb 산출물 생성

**수정이 필요할 때**: Dify / Jenkins 버전업, OS 패키지 추가, seed 자원 경로 변경.

### 2.4 `entrypoint.sh`

> 🐳 **[컨테이너 내부]** — `docker run` 이 PID 1 로 자동 실행.

**목적**: seed 복사 + supervisord 기동 + provision 호출 + NODE_SECRET 출력.

**동작 요약** (타임라인):

1. **seed** — `/data/.initialized` 플래그 없으면 `/opt/seed/*` → `/data/` 복사 (pg/jenkins/dify 초기 상태)
2. **supervisord 기동** — [§2.6 supervisord.conf](#26-supervisordconf) 로드, 10 개 프로세스
3. **헬스 대기** — dify-api `/console/api/setup` + dify-web + Jenkins 모두 HTTP 200 까지 (최대 10분)
4. **provision 호출** — `/data/.app_provisioned` 없으면 `bash /opt/provision.sh`
5. **NODE_SECRET 출력** — `/computer/mac-ui-tester/slave-agent.jnlp` 에서 추출해 로그로 출력 (재시작 때마다 재출력)
6. **supervisord foreground wait**

**수정이 필요할 때**: 기동 순서 변경, 추가 seed 경로, 헬스 대기 로직 변경.

### 2.5 `provision.sh`

> 🐳 **[컨테이너 내부]** — entrypoint 가 `/data/.app_provisioned` 플래그 없을 때 자동 호출.

**목적**: Dify + Jenkins 프로비저닝 (관리자 / 플러그인 / 모델 / Chatflow / API Key / Pipeline Job / Node 생성). 수동 재실행도 가능:

```bash
docker exec dscore.ttc.playwright bash /opt/provision.sh
```

**동작 요약**:

| 단계 | 내용 |
|------|------|
| 1. 서비스 헬스체크 | dify-web / dify-api / Jenkins HTTP 200 대기 |
| 2-1. Dify 관리자 생성 | POST `/console/api/setup` |
| 2-2. Dify 로그인 | POST `/console/api/login` → 쿠키 + CSRF 토큰 |
| 2-3a. Ollama 플러그인 설치 | 로컬 `.difypkg` 업로드 |
| 2-3b. 모델 공급자 등록 | `base_url = host.docker.internal:11434`, 모델 id = `$OLLAMA_MODEL` |
| 2-3c. credential_id swap | Dify 내부 `provider_models.credential_id` 를 신규 레코드로 교체 (plugin-daemon 캐시 우회) |
| 2-3d. Redis FLUSH | `provider_model_credentials:*` + 모델 다이제스트 키 삭제 |
| 2-3e. dify-api + plugin-daemon 재기동 | `supervisorctl restart` + **HTTP readiness 대기 (최대 300s)** |
| 2-4. Chatflow DSL import | `dify-chatflow.yaml` 읽어 Dify App 생성 |
| 2-5. Publish + API Key 발급 | Dify App 을 publish → Jenkins Credentials 에 등록 |
| 3. Jenkins 설정 | 플러그인 4 개 검증 / Credentials / Pipeline Job / Node `mac-ui-tester` 생성 |

**주요 env** (entrypoint 가 주입):

- `DIFY_URL`, `JENKINS_URL`, `DIFY_EMAIL`, `DIFY_PASSWORD`, `JENKINS_ADMIN_USER/PW`
- `OFFLINE_DIFY_PLUGIN_DIR`, `OFFLINE_DIFY_CHATFLOW_YAML`, `OFFLINE_JENKINS_PIPELINE`
- `OLLAMA_MODEL`, `OLLAMA_BASE_URL`

**완전 재프로비저닝**:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

**수정이 필요할 때**: 초기 설정 플로우 변경, 새 Chatflow / Credential / Job 추가, Dify API 변경 대응.

### 2.6 `supervisord.conf`

> 🐳 **[컨테이너 내부]** — `supervisord` 가 PID 2 부터 `/etc/supervisor/supervisord.conf` 로 로드.

**목적**: 컨테이너 내부 10 개 프로세스의 기동 순서 / env / 로그 경로 정의.

**실행 시점**: `entrypoint.sh` 가 호출하는 `supervisord` 가 로드 (`/etc/supervisor/supervisord.conf`).

**기동 순서** (priority 낮을수록 먼저):

| priority | 프로그램 | 포트 | 역할 |
|----------|----------|------|------|
| 100 | postgresql | 5432 | Dify DB 5 개 |
| 100 | redis | 6379 | Dify 큐 + Celery broker |
| 100 | qdrant | 6333 | Dify 벡터 스토어 |
| 200 | dify-plugin-daemon | 5002 | Ollama 플러그인 gRPC |
| 300 | dify-api | 5001 | gunicorn + gevent 1 worker (첫 응답 60-90s 걸릴 수 있음) |
| 300 | dify-worker / dify-worker-beat | — | Celery 워커 + 스케줄러 |
| 300 | dify-web | 3000 | Next.js 프론트 |
| 400 | nginx | 18081 | api/web 프록시 ([§2.7](#27-nginxconf)) |
| 500 | jenkins | 18080, 50001 | Jenkins controller |

**컨테이너 내부에 없는 프로세스**: `ollama`, `jenkins-agent` — 호스트에서 실행.

**주요 env (dify-api)**: `DB_HOST=127.0.0.1`, `REDIS_HOST=127.0.0.1`, `PLUGIN_DAEMON_URL=http://127.0.0.1:5002`, `SERVER_WORKER_CONNECTIONS=1000`, `GUNICORN_TIMEOUT=360`.

**수정이 필요할 때**: 서비스 추가/제거, gunicorn 튜닝, 환경변수 변경.

### 2.7 `nginx.conf`

> 🐳 **[컨테이너 내부]** — `[program:nginx]` 가 시작 시 로드.

**목적**: 컨테이너 외부 포트 **:18081** 로 들어온 요청을 Dify web / api 로 프록시.

**라우팅 규칙**:

- `/console/api/*`, `/api/*`, `/v1/*`, `/files/*` → `http://127.0.0.1:5001` (dify-api)
- 그 외 (`/install`, `/apps`, ...) → `http://127.0.0.1:3000` (dify-web)

**수정이 필요할 때**: Dify 가 새로운 API prefix 를 추가하거나 CORS 설정을 바꿀 때.

### 2.8 `pg-init.sh`

> 🔧 **[빌드 타임]** — Dockerfile 빌드 단계에서만 실행. 런타임에는 쓰이지 않음.

**목적**: PostgreSQL 초기화 (initdb + Dify DB 5 개 사전 생성) 를 빌드 타임에 미리 수행해 `/opt/seed/pg/` 에 박아두기.

**왜 빌드 타임에?** 런타임에 initdb + 5 개 DB 생성 + extension 로드까지 하면 첫 기동에 5-10분 추가됨. 빌드 타임에 한 번 만들어 둔 걸 첫 기동에 `/opt/seed/pg/` → `/data/pg/` 로 복사만 하면 끝.

**수정이 필요할 때**: Dify 가 요구하는 DB 목록/이름/extension 이 바뀔 때 (major 업그레이드).

### 2.9 `requirements.txt`

> 🔧 **[빌드 타임]** — Dockerfile 의 `pip install -r requirements.txt` 단계.

**목적**: 컨테이너의 Dify api / worker 전용 Python 의존성 (pinned). Dify 애플리케이션 코드 (`/opt/dify-api/api/`) 의 `.venv` 에 설치된다.

**왜 별도 파일?** Dify 공식 이미지에서 애플리케이션 코드 (`/opt/dify-*`) 는 복사하지만 `.venv` 는 아키텍처/glibc 차이로 재사용 불가 → 이 파일로 해당 환경의 네이티브 wheel 을 재설치.

**수정이 필요할 때**: Dify 버전업 시 Dify 공식 repo 의 `api/requirements.txt` (또는 `pyproject.toml`) 로부터 재생성.

### 2.10 seed 자원 (빌드 시점에 이미지로 들어가는 파일들)

> 📖 **[참조]** — 직접 실행 파일 아님. Dockerfile 이 빌드 시 COPY 하는 자료들.

이 폴더 안의 파일 + 빌드 산출물:

| 경로 | 역할 | 빌드 시점에 이미지 어디로 |
|------|------|----------------------------|
| `ZeroTouch-QA.jenkinsPipeline` | Jenkins Pipeline DSL (`agent { label 'mac-ui-tester' }` + `zero_touch_qa` 실행) | `/opt/ZeroTouch-QA.jenkinsPipeline` |
| `dify-chatflow.yaml` | Dify App `ZeroTouch QA Brain` 의 Chatflow DSL (Planner / Healer LLM 노드 포함) | `/opt/dify-chatflow.yaml` |
| `zero_touch_qa/` (Python 패키지) | 호스트 agent 가 `python -m zero_touch_qa` 로 실행 (Playwright + Dify client) | 이미지에 포함되지 않음 — **호스트에서 `SCRIPTS_HOME` 경로로 import** |
| `jenkins-plugins/*.hpi` | `build.sh [1/4]` 가 수집한 Jenkins 플러그인 | `/usr/share/jenkins/ref/plugins/` |
| `dify-plugins/*.difypkg` | `build.sh [2/4]` 가 받은 Dify Ollama 플러그인 | `/opt/seed/dify-plugins/` |
| `jenkins-init/basic-security.groovy` | 첫 기동 시 Jenkins 관리자 생성 Groovy init | `/usr/share/jenkins/ref/init.groovy.d/` |

---

## 3. 운영 가이드

### 3.1 재시작 / 중지 / 제거

```bash
# 컨테이너만 재시작 (상태 유지)
docker restart dscore.ttc.playwright             # 30-60초

# 일시 중지
docker stop dscore.ttc.playwright

# 컨테이너 제거 (볼륨 유지)
docker rm -f dscore.ttc.playwright

# 완전 초기화 (주의 — Dify DB / Jenkins config 다 사라짐)
docker volume rm dscore-data
```

**호스트 agent 정리** (Mac / WSL 공통):

```bash
# agent 터미널에서 Ctrl+C 후
rm -rf ~/.dscore.ttc.playwright-agent
```

### 3.2 호스트 agent 재연결

컨테이너를 재시작하면 JNLP 세션이 끊기므로 agent 도 다시 연결해야 한다. 같은 스크립트를 그냥 다시 실행하면 된다 (step 0-A 가 이전 agent 정리, 0-B 가 새 NODE_SECRET 자동 추출):

```bash
./mac-agent-setup.sh       # Mac
./wsl-agent-setup.sh       # WSL2
```

### 3.3 로그

```bash
# 컨테이너 내부 로그 (서비스별 분리)
docker exec dscore.ttc.playwright tail -f /data/logs/dify-api.log
docker exec dscore.ttc.playwright tail -f /data/logs/jenkins.log
docker exec dscore.ttc.playwright supervisorctl status

# 호스트 agent 로그 (--redeploy 로 기동한 경우)
tail -f /tmp/dscore-agent.log
```

### 3.4 백업 / 복원 / 폐쇄망 반입 / 업그레이드

#### 운영 데이터 백업 (권장 절차)

`backup-volume.sh` / `restore-volume.sh` 스크립트가 dscore-data 볼륨의 전체 누적 상태
(PG 메타 + Qdrant 벡터 + Jenkins job + 챗봇 conversation) 를 일관성 있게 export / import 한다.

내부 동작: supervisorctl 로 모든 서비스 quiesce → busybox 컨테이너로 tar gzip → 서비스
재기동. DB 트랜잭션 일관성 보장.

**백업**:

```bash
cd playwright-allinone
./backup-volume.sh                              # 자동 파일명: dscore-data-YYYYMMDD-HHMMSS.tar.gz
./backup-volume.sh /custom/path/backup.tar.gz   # 사용자 지정 경로
```

**복원**:

```bash
docker stop dscore.ttc.playwright || true
docker rm  dscore.ttc.playwright || true

cd playwright-allinone
./restore-volume.sh dscore-data-20260427-120000.tar.gz
# 기존 볼륨이 있으면 거절 — wipe 후 복구하려면 --fresh 추가:
# ./restore-volume.sh --fresh dscore-data-20260427-120000.tar.gz

# 복구 후 docker run 으로 재기동 (Step 2 동일 옵션 + AGENT_NAME)
```

#### 폐쇄망 반입 (image + volume 쌍)

운영 누적 지식이 있는 환경 (KB 문서, 챗봇 대화 이력 등) 을 폐쇄망으로 옮길 때:

| 머신 | 단계 | 산출 / 입력 |
| --- | --- | --- |
| 빌드/온라인 | `./build.sh` | `dscore.ttc.playwright-YYYYMMDD-HHMMSS.tar.gz` (image) |
| 빌드/온라인 | `./backup-volume.sh` | `dscore-data-YYYYMMDD-HHMMSS.tar.gz` (volume) |
| → 반입 | 두 파일 모두 폐쇄망 머신으로 (USB / SCP / artifact) | |
| 폐쇄망 | `docker load -i dscore.ttc.playwright-*.tar.gz` | image 등록 |
| 폐쇄망 | `./restore-volume.sh dscore-data-*.tar.gz` | volume 복구 |
| 폐쇄망 | `docker run -d ...` (Step 2 옵션) | 컨테이너 기동, 누적 상태로 즉시 운영 |

엔트리포인트가 `/data/.app_provisioned` 마커 발견 시 provision 단계 skip — 기존 KB / Jenkins
job / 챗봇 그대로 복구 시작. 신규 image 코드 (chatflow YAML 변경 등) 가 반영되려면 별도
재import 필요 (자동화 미지원, 운영자 수동 작업).

#### Image 단독 반입 (KB seed 자동 업로드 — B 보조)

volume 백업 없이 image 만 반입한 경우에도 빈 KB 가 아닌 사내 baseline 문서로 시작 가능:
`provision.sh` 가 첫 부팅 시 image baked-in `/opt/seed/kb-docs/` (= 빌드 시점의
[`examples/test-planning-samples/`](examples/test-planning-samples/)) 를 자동으로 두 KB
에 업로드 + 인덱싱. 운영자는 GUI 로 추가 업로드만 진행하면 됨.

#### `--fresh` 의미 명확화

- `build.sh --redeploy --fresh`: **개발 / 트러블슈팅용**. dscore-data 볼륨까지 wipe →
  KB / Jenkins job / 챗봇 이력 모두 폐기. 운영 반입에는 사용 금지.
- `build.sh --redeploy` (without `--fresh`): 기존 볼륨 보존. provision.sh 가 마커
  (`/data/.app_provisioned`) 발견 시 KB / chatflow 재생성 skip — 신 image 의 코드 변경
  (예: chatflow YAML 갱신) 은 자동 반영 안 됨. 명시적 재import 필요.

#### 업그레이드

```bash
# 누적 데이터 백업 먼저 (반드시!)
./backup-volume.sh

# 컨테이너 정지 + 새 image 로드
docker stop dscore.ttc.playwright
docker rm dscore.ttc.playwright
docker load -i dscore.ttc.playwright-new.tar.gz

# 기존 볼륨 그대로 사용해 재기동 (Step 2 docker run 옵션)
# 새 image 의 코드 변경 (예: chatflow YAML) 반영이 필요하면 manual re-import
# 또는 --fresh 후 ./restore-volume.sh 로 부분 복구 (KB 만 옮겨오는 식 — 별도 절차).
```

호스트 `~/.dscore.ttc.playwright-agent` 는 백업 불필요 — agent-setup 재실행으로 복구됨.

### 3.5 프로비저닝 체크리스트

프로비저닝이 제대로 됐는지 항목별로 확인:

| # | 항목 | 확인 명령 |
|---|------|-----------|
| 1 | Dify 관리자 | `curl -fsS http://localhost:18081/console/api/setup \| jq .setup_status` → `"finished"` |
| 2 | Dify Ollama 플러그인 | `docker exec dscore.ttc.playwright ls /data/dify/plugins/packages` 에 `langgenius-ollama-*` |
| 3 | Ollama 모델 등록 (host URL) | DB 조회로 `base_url: host.docker.internal:11434` |
| 4 | Chatflow | Dify UI 에 `ZeroTouch QA Brain` 앱 (또는 `ZeroTouch-QA`) |
| 5 | Dify API Key | `docker logs dscore.ttc.playwright \| grep "API Key 발급 완료"` |
| 6 | Jenkins 플러그인 4 개 | UI `/pluginManager/` 또는 `curl … /pluginManager/api/json?depth=1` |
| 7 | Jenkins Credentials | `curl -u admin:pw … /credentials/store/system/domain/_/api/json` 에 `dify-qa-api-token` |
| 8 | Pipeline Job | Dashboard 에 `ZeroTouch-QA` |
| 9 | **Node online** | `curl -u admin:pw … /computer/mac-ui-tester/api/json \| jq .offline` → `false` (Step 3 이후) |

### 3.6 관리자 비밀번호 변경

| 상황 | 방법 |
|------|------|
| 첫 배포 직전 | docker run 에 env 주입: `-e JENKINS_ADMIN_PW='<pw>' -e DIFY_PASSWORD='<pw>'` (`.initialized` 플래그가 없을 때만 적용) |
| 운영 중 Jenkins | UI → People → `admin` → Configure → Password |
| 운영 중 Dify | 우상단 계정 → Settings → Account → Password |

### 3.7 Ollama 모델 관리 (호스트)

컨테이너에는 Ollama 가 없으니 **모든 모델 작업은 호스트의 `ollama` 명령**.

```bash
# 현재 상태 (Mac / Windows 동일)
ollama list
ollama show gemma4:26b
```

**새 모델로 교체**:

```bash
# 1) 호스트에서 pull
ollama pull llama3.1:8b

# 2) Chatflow DSL 의 모델명도 함께 갱신 (Planner + Healer 두 군데)
#    playwright-allinone/dify-chatflow.yaml 의 `name: "gemma4:26b"` 두 줄을 새 모델명으로 치환

# 3) 컨테이너 재생성 — 새 OLLAMA_MODEL 로
docker rm -f dscore.ttc.playwright
docker run -d --name dscore.ttc.playwright ... -e OLLAMA_MODEL=llama3.1:8b ... dscore.ttc.playwright:latest

# 4) 새 yaml 을 컨테이너에 주입 + 재프로비저닝
docker cp playwright-allinone/dify-chatflow.yaml dscore.ttc.playwright:/opt/dify-chatflow.yaml
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright

# 5) agent 재연결
./mac-agent-setup.sh       # 또는 wsl-agent-setup.sh
```

> **Chatflow UI 에서 바꾸는 방법도 가능**: Dify `ZeroTouch QA Brain` 앱의 LLM 노드 (Planner / Healer) 에서 Model 드롭다운 → Publish. 하지만 이후 `.app_provisioned` 가 지워져 재프로비저닝이 돌면 yaml 의 값으로 되돌아간다. 영구 변경은 **yaml 을 수정해야 한다.**

**`dify-chatflow.yaml` 자체를 수정한 뒤 반영하는 절차**:

```bash
# 1) 로컬 수정본을 컨테이너에 주입
docker cp playwright-allinone/dify-chatflow.yaml dscore.ttc.playwright:/opt/dify-chatflow.yaml

# 2) 자동 프로비저닝 완료 마커 제거
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned

# 3) 컨테이너 재기동
docker restart dscore.ttc.playwright

# 4) 로그에서 재프로비저닝 확인
docker logs -f dscore.ttc.playwright
```

로그에서 `ZeroTouch QA Brain` import/publish 관련 메시지와 `프로비저닝 완료`를 확인한다. 이후 Dify 콘솔에서 앱을 열어 Planner/Healer 프롬프트가 기대한 값으로 반영됐는지 다시 확인한다.

**모델 선택 기준**:

| 모델 | 크기 | 호스트 RAM | 용도 |
| --- | --- | --- | --- |
| `gemma4:26b` | ~17GB | 24-32GB | **기본 (Sprint 5+)** — 14대 DSL 시나리오를 안정적으로 emit. 추론 30-90s/call (Apple M-series Metal) |
| `gemma4:e4b` | ~4GB | 6-8GB | 경량 (Sprint 1 baseline) — 빠르지만 14 step 시나리오에서 결정성 부족 |
| `llama3.1:8b` | ~4.7GB | 8-10GB | 대안 — 복잡한 SRS 에서 한 번에 맞출 확률 ↑ |
| `qwen2.5:7b` | ~4.4GB | 8-10GB | 다국어 (영/중 혼합 SRS 에 유리) |

#### LLM 비결정성 방어 스택

Dify Planner (기본 `gemma4:26b`, 26B 파라미터 — Sprint 5 §10.2 결정) 는 4 계층 안전망으로 보호돼 단독으로도 일관된 시나리오를 생성한다. 모델을 바꾸기 전에 **이 스택 전체를 먼저 이해**하자.

| # | 계층 | 위치 | 하는 일 |
| --- | --- | --- | --- |
| 1 | **프롬프트 결정성** | `dify-chatflow.yaml` Planner 노드 | `temperature=0.1` + `[⚠️ 최우선 규칙 — STEP 1 은 예외 없이 navigate]` 섹션 + `[✅ 예시]` 블록 (few-shot) 로 출력 구조를 강제 |
| 2 | **시나리오 검증 + 자동 재시도** | `zero_touch_qa/__main__.py` `_validate_scenario` / `_prepare_scenario` | Dify 응답을 받은 즉시 (a) 배열 비어있지 않음, (b) 모든 step 의 action 이 9 대 표준, (c) navigate/wait 이외는 target 존재 를 검증. 실패하면 **최대 3 회까지 재생성** (5s / 10s / 15s backoff) |
| 3 | **Guard 자동 prepend** | `zero_touch_qa/__main__.py` line 77-88 | 3 회 재시도 성공 후에도 `scenario[0].action != "navigate"` 면 `TARGET_URL` 로 step 1 navigate 를 자동 prepend. 콘솔에 `[Guard] scenario[0].action != navigate — TARGET_URL 로 navigate step 자동 prepend` |
| 4 | **Runtime healing** | `executor.py` LocalHealer + Dify `/v1/chat-messages` 치유 요청 | 실행 중 selector 가 실패하면 DOM 스냅샷을 Dify 에 보내 교정 target 을 받아 재시도. selector 수준 오류는 이 단계에서 대부분 복구 |

**"더 큰 모델로 교체" 는 위 4 단계가 모두 막은 후에도 **같은 SRS 로 반복 재시도마다 FAIL** 할 때 비로소 고려.** 실무상 그 전에 "SRS 를 더 명시적으로 재작성" 만으로 해결되는 경우가 대부분.

**재시도 로그 예시** (정상 케이스가 한 번 실패 후 복구되는 모습):

```text
[Retry 1/3] 시나리오 수신/검증 실패 — step[1].action 이 유효하지 않음: None (다음 시도까지 5s 대기)
[Dify] 시나리오 수신 (4스텝) — attempt 2/3 성공
[Step 1] navigate -> PASS
...
```

3 회 모두 실패 시 Pipeline 은 "시나리오 구조 검증 실패 (3 회 재시도 모두 실패): …" 메시지와 함께 즉시 종료된다 (무한 대기 없음). 이 경우의 대응은 [§4.9](#49-pipeline-이-failure-로-끝나지만-실제로는-스텝이-pass-하는-것-같다) 참조.

**Ollama 런타임 튜닝** (영구 상주 / 동시 로드):

Mac:

```bash
brew services stop ollama
launchctl setenv OLLAMA_KEEP_ALIVE -1
launchctl setenv OLLAMA_MAX_LOADED_MODELS 2
brew services start ollama
```

Windows (PowerShell):

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', '-1', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '2', 'User')
Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
```

### 3.8 Jenkins Pipeline 을 API 로 트리거하기

CI/스크립트 연동이 필요하면 UI 대신 `buildWithParameters` REST 로 호출. **`DOC_FILE` 은 File parameter 라 API 호출 시 빈 파일이라도 반드시 첨부해야 500 이 나지 않는다**:

```bash
EMPTY=/tmp/empty.txt; : > "$EMPTY"
CRUMB_HEADER=$(curl -sS -u admin:password \
  "http://localhost:18080/crumbIssuer/api/json" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["crumbRequestField"]+":"+d["crumb"])')

curl -sS -u admin:password -X POST \
  -H "$CRUMB_HEADER" \
  "http://localhost:18080/job/ZeroTouch-QA/buildWithParameters" \
  -F "RUN_MODE=chat" \
  -F "TARGET_URL=https://www.naver.com" \
  -F "SRS_TEXT=검색창에 si qa 입력 후 검색 실행하고 결과 확인" \
  -F "HEADLESS=true" \
  -F "DOC_FILE=@${EMPTY}"
```

UI 에서 기동할 때는 DOC_FILE 을 비워 두어도 문제없다 (Jenkins 가 자동으로 빈 값 처리).

### 3.9 v4.1 운영 SLA / 모니터링 (Sprint 4 + Sprint 6 closure)

Sprint 4 (4A/4B/4C) + Sprint 6 (chat 모드 결정성) 가 닫히면서 v4.1 운영 표준이 다음과 같이 정해졌다.

#### 산출물 7종 (빌드별 `qa_reports/build-${BUILD_NUMBER}/`)

| 파일 | 의미 | 용도 |
| --- | --- | --- |
| `index.html` | Zero-Touch QA Report | step 결과 + 운영 지표 섹션 (Planner/Healer/SLA/heal/flake/pytest) |
| `run_log.jsonl` | step 단위 실행 로그 | 회귀 / heal trace 추적 |
| `scenario.json` / `scenario.healed.json` | 입력 / healed 결과 시나리오 | 재실행, healing diff |
| `regression_test.py` | 결정론적 회귀 (Playwright 라인) | 향후 Dify 없이 같은 시나리오 재실행 |
| `pytest_integration.xml`, `pytest_native.xml` | JUnit 결과 | Jenkins **JUnit Trend** 자동 추세 |
| `llm_calls.jsonl`, `llm_sla.json` | Dify Planner/Healer 호출 metric (raw + 집계) | LLM SLA baseline / 임계값 추적 |
| `screenshots/*.png` | 단계별 스냅샷 | 재현 디버깅 |

`buildDiscarder` 가 30 일 / 100 빌드 보존을 강제하며, JUnit XML 의 추세는 Jenkins Job 페이지의 **Test Result Trend** 그래프에서 자동 노출된다.

#### LLM SLA 추적

Sprint 4A 의 `llm_calls.jsonl` 계측 + Sprint 4C 의 `aggregate_llm_sla` hook 으로, 매 빌드 종료 시 자동으로 `llm_sla.json` 이 만들어진다. 본 파일은 `index.html` 의 운영 지표 섹션에 다음 형태로 노출된다:

- `latency_ms` p50 / p95 / p99
- `total_calls`, `timeout_count`, `timeout_rate`, `retry_total`
- `by_kind.planner` / `by_kind.healer` 분리 metric

모델 변경 또는 hardware 변경 시 `llm_sla.json` 의 p95 latency 가 baseline 대비 +30% 이상 상승하면 회귀로 본다. 임계값 baseline 은 첫 1~2 회 빌드의 metric 으로 확정한다.

#### mock 안전장치 (S4B-09 완료 — 운영 필수)

`mock_status` / `mock_data` 의 URL pattern 이 운영 host 와 충돌하지 않도록 다음 환경변수를 사용한다:

| 환경변수 | 의미 |
| --- | --- |
| `TARGET_URL` | 현재 빌드의 운영 host. 동일 host 와 매칭되는 mock pattern 은 빌드 실패 |
| `MOCK_BLOCKED_HOSTS` | `,` 구분 추가 차단 host 목록 (예: `api.production.example.com,internal.example.com`) |
| `MOCK_OVERRIDE=1` | 명시적 우회 (감사 로그 남김). 디버깅 외에는 사용 금지 |

#### healing / heal 비용 모니터링

`llm_sla.json` 의 `by_kind.healer` 가 Dify heal 호출 횟수와 평균 latency 를 보여준다. 단일 시나리오에서 heal 호출이 step 수의 30% 를 초과하면 시나리오 품질 / fixture 안정성 문제로 본다.

#### Convert 14대 모드 사용 (Sprint 4C 완료)

Playwright codegen 으로 녹화한 14대 액션 (`set_input_files` / `drag_to` / `scroll_into_view_if_needed` / `page.route(... fulfill ...)` 포함) 을 그대로 Jenkins 에 업로드하면 `RUN_MODE=convert` 가 정규식 파서로 14대 DSL 로 변환 후 즉시 실행한다. LLM 호출 0. `test/recorded-14actions.py` 가 codegen 형식 reference fixture 다.

#### DIFY_API_KEY 갱신

Dify 콘솔에서 새 app key 발급 → Jenkins → Manage Credentials → `DIFY_API_KEY` 값 갱신 → 빈 빌드 트리거하여 Stage 2.5 (Dify health probe) 가 PASS 하는지 확인. health probe 가 401/timeout 이면 즉시 Job 실패하므로 운영 영향 없음.

#### agent 동시성 한계

단일 Mac/WSL agent 는 동시 빌드 1 개를 권장한다. JOB 의 `disableConcurrentBuilds()` 또는 agent label 의 `numExecutors=1` 로 제한. 동시 2+ 빌드는 artifacts 디렉토리 충돌, 브라우저 자원 부족으로 false fail 위험이 있다.

#### Sprint 6 chat 모드 결정성 (Sprint 5 §10.5 한계 해소)

Sprint 5 까지 chat 모드는 "best-effort 의미 시연" 으로 위치 (결정적 14 액션 검증은 execute 모드 책임). Sprint 6 에서 다섯 결함을 표적 수정해 chat 모드도 17/17 결정적 PASS 를 달성:

| 결함 | 보강 |
| --- | --- |
| Meta-reasoning leak (action 필드에 `'verify, target: ...'` 같은 자기 설명) | `_sanitize_scenario` 가 leading valid token 추출 회복 |
| Compound SRS ("X 한 뒤 Y") 가 첫 액션만 emit | Default SRS_TEXT 16 atomic 항목 + Planner prompt 의 N=16 1-shot 예시 |
| `mock_*` 자율 drop (사용자 가시성 낮다고 판단) | Planner prompt 에 "mock_* drop 금지" 명시 룰 |
| Healer mutation 표면 협소 (action 변경 절대 금지 → 의미 매핑 미스 회복 불가) | Whitelisted 의미 등가 전이 4쌍 (`select↔fill`, `check↔click`, `click↔press`, `upload↔click`) 만 허용 — 그룹간 변경은 거절해 false-PASS 차단 |
| Press heuristic 과확장 (fixture 단순 DOM 업데이트도 search 실패로 오판) | `localhost`/`file://` 환경에서는 strict URL/탭 체크 skip |

**추가 보강 — `<think>` 토큰 한도 초과**:

- Planner system prompt 에 `<think>`/`<thinking>`/`<reasoning>` 출력 금지 룰 추가.
- Planner `max_tokens` 4096 → **8192**, `OLLAMA_CONTEXT_SIZE` 8192 → **12288** (1.5x 안전 마진, KV cache 비용 trade-off 로 16384 대신 12288 채택).

**측정 결과** — 동일 default SRS 로 chat 3 회 연속:

| Build | Result | Steps | Duration | Retry |
| --- | --- | --- | --- | --- |
| #1 | SUCCESS | 17/17 PASS | 126.0s | 0 |
| #2 | SUCCESS | 17/17 PASS | 89.4s | 0 |
| #3 | SUCCESS | 17/17 PASS | 88.0s | 0 |

3/3 first-try PASS, 0 retry, 평균 dur 101s. Sprint 5 §10.5 의 "chat = best-effort" 위치 사실상 폐기.

### 3.10 Test Planning RAG 트랙 — 운영 가이드

**Test Planning Brain** 은 별도 RAG 트랙으로, 사용자 자연어
요청을 받아 **테스트 계획서 / 자연어 시나리오 / 14-DSL JSON** 을 자동 생성하는 신규
챗봇이다. 자세한 설계는 [`PLAN_TEST_PLANNING_RAG.md`](PLAN_TEST_PLANNING_RAG.md) 참조.

#### 자동 생성되는 인프라

`provision.sh` 가 fresh 부팅 시 다음을 자동 생성:

| 항목 | 이름 / ID | 비고 |
| --- | --- | --- |
| Embedding 모델 등록 | `bona/bge-m3-korean:latest` | `text-embedding` type. PLAN §2.2.2 |
| Workspace default-model | LLM=`gemma4:26b`, embedding=위와 동일 | KB high_quality 생성 전제 |
| Knowledge Base #1 | `kb_project_info` | spec / 기획서 / API 문서. retrieval #1 대상 |
| Knowledge Base #2 | `kb_test_theory` | 테스트 설계 기법 / 회귀 정책. retrieval #2 대상 |
| **KB seed 문서 자동 업로드** | image baked-in `/opt/seed/kb-docs/` | image 단독 반입 시에도 비어 있지 않은 KB 시작 가능. 멱등 (이미 존재하면 skip) |
| Dify 앱 | `Test Planning Brain` (advanced-chat) | retrieval ×2 → LLM (gemma4:26b) → answer |
| Dify 공개 chat URL | 두 앱 모두 `prompt_public:true` 활성화 | `http://localhost:18081/chat/<code>` 즉시 접근 |
| Jenkins credential | `dify-test-planning-api-token` | API key 선등록 (사용처는 후속 자동화 트랙) |

#### KB 문서 업로드 (Dify console GUI)

본 트랙은 GUI 직접 업로드 정책 (PLAN §4.1). 자동화는 후속 트랙.

1. 브라우저 → `http://localhost:18081` → admin 로그인 (`admin@example.com` / `Admin1234!`).
2. 좌측 메뉴 → **Knowledge** → 두 KB (`kb_project_info`, `kb_test_theory`) 확인.
3. KB 클릭 → **Add documents** → 파일 드래그.
4. 청킹 모드 — Automatic 또는 Custom (chunk_size=500, overlap=50 권장).
5. 인덱싱 시작 → 완료까지 5분 미만 (5 청크 기준).

#### 지원 형식 (3 형식)

| 형식 | 처리 | 비고 |
| --- | --- | --- |
| `pdf` | PyMuPDF | 표 / 이미지 OCR 미사용 |
| `docx` | python-docx | 헤딩 구조 보존 |
| `csv` | Dify CSV | row-as-chunk |

**PPTX 는 미지원** — Dify 1.13.3 의 `ETL_TYPE=dify` 분기에 PPTX 처리가 없음 (PLAN §3.1).
PPTX 가 필요하면 사용자가 PDF / Markdown 으로 변환 후 업로드.

#### 변경 파일만 재인덱싱 (PLAN §4.2)

Dify console → 대상 KB → 문서 목록 → 변경된 문서 우측 메뉴 → **다시 인덱싱**.
전체 KB 재인덱싱은 청킹 정책 자체가 바뀐 경우만 사용.

#### 챗봇 접근 — 두 경로

**(1) 공개 chat URL** (anonymous, 익명 사용 가능 — provision.sh 가 자동 활성화):

```text
http://localhost:18081/chat/<code>
```

`<code>` 는 fresh provision 시점에 무작위 생성. 발급된 code 를 확인하려면 console GUI
(앱 → 모니터링 → 공개 / API access) 또는 다음 curl:

```bash
PASS_B64=$(printf '%s' 'Admin1234!' | base64)
COOKIES=$(mktemp)
curl -sf -c "$COOKIES" -X POST "http://localhost:18081/console/api/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"admin@example.com\",\"password\":\"$PASS_B64\",\"remember_me\":true}" >/dev/null
CSRF=$(awk '$6 == "csrf_token" || $6 == "__Host-csrf_token" { print $7; exit }' "$COOKIES")
curl -sS -b "$COOKIES" -H "X-CSRF-Token: $CSRF" \
  "http://localhost:18081/console/api/apps?page=1&limit=10" | \
  python3 -c "import json,sys
d=json.load(sys.stdin)
for a in d.get('data',[]):
    s=a.get('site') or {}
    print(f\"  {a.get('name')}: http://localhost:18081/chat/{s.get('code','(disabled)')}\")"
```

⚠️ **포트 18081 필수** — Dify 는 컨테이너 내부 nginx 가 18081 에서 서빙. `http://localhost/chat/...`
(포트 80) 은 동작 안 함.

**(2) Studio (관리자 작업용)**:

좌측 메뉴 → **Studio** → `Test Planning Brain` 클릭 → 우측 상단 **API access** 또는
**Run** 으로 즉시 테스트.

```text
사용자 입력 변수:
  user_query: 결제 모듈 테스트 계획서를 만들어줘
  output_mode: plan
  target_module: 결제 모듈
```

| output_mode | 출력 형식 |
| --- | --- |
| `plan` | IEEE 829-lite 8 섹션 Markdown (7 + traceability 표) |
| `scenario_natural` | Given/When/Then BDD 시나리오 + 근거 줄 |
| `scenario_dsl` | 순수 14-DSL JSON list + `# traceability:` 주석 (자동 파서 추출 가능) |
| `both` | 위 셋 모두. 14-DSL 은 `<!-- BEGIN/END SCENARIO_DSL -->` strict fence 안 |

#### `scenario_dsl` 출력을 ZeroTouch-QA execute 로 연결 (수동)

1. Dify Web UI 에서 `output_mode: scenario_dsl` 호출 → 응답 받기.
2. 응답에서 `# traceability:` 이전까지의 JSON list 추출.
3. JSON 을 파일로 저장 (예: `scenario.json`).
4. Jenkins **ZeroTouch-QA** Job 트리거 — `RUN_MODE=execute`, `DOC_FILE=scenario.json`.
5. 실 환경 selector 가 안 맞으면 사람이 selector 검토 / 수정 후 재시도.

자동화 (webhook 등) 는 PLAN §7.2 의 "Test Planning → ZeroTouch-QA AutoLink" 후속 트랙
범위.

#### Embedding 모델 교체 금지 (Qdrant collection lock)

KB 첫 인덱싱 시점에 차원이 고정 (현재 1024차원). 모델 변경 시 KB 재생성 + 모든 문서
재업로드 필요. PLAN §4.4.2 도서관 색인 시스템 비유 참조.

#### 샘플 문서 세트

[`examples/test-planning-samples/`](examples/test-planning-samples/) 에 4 파일 (3 형식)
샘플 제공:

- `spec.md` (Markdown) → `kb_project_info`
- `feature_login.md` (Markdown) → `kb_project_info`
- `api.csv` (CSV) → `kb_project_info`
- `test_theory_boundary_value.md` (Markdown) → `kb_test_theory`

세부 절차는 해당 디렉토리의 README 참조.

---

## 4. 트러블슈팅

### 4.1 컨테이너가 기동 직후 죽는다

```bash
docker logs dscore.ttc.playwright | tail -50
```

- 메모리 부족 → Docker Desktop 메모리 할당 ↑
- PG init 실패 → `/data/logs/postgresql.err.log`. 권한 문제면 `docker volume rm dscore-data` 후 재기동

### 4.2 Pipeline 이 `'mac-ui-tester' is offline` 에서 대기

```bash
# Node 상태 확인
curl -sS -u admin:password http://localhost:18080/computer/mac-ui-tester/api/json | grep -oE '"offline":(true|false)'
```

**원인 A**: 호스트 agent 미연결 → `./<mac|wsl>-agent-setup.sh` 재실행.

**원인 B**: 이전 agent 프로세스가 좀비로 남음. 스크립트 step 0-A 가 자동 정리하므로 그냥 다시 실행하면 해결.

**원인 C**: JNLP 포트 50001 방화벽.

- Mac: `lsof -i :50001`, `nc 127.0.0.1 50001`
- WSL2: `ss -tlnp | grep 50001`. Windows Defender 방화벽이 차단 시 관리자 PowerShell 에서 규칙 추가

**원인 D (가장 흔함)**: 스크립트 실행 권한 없음 → `chmod +x *.sh` 후 재실행.

### 4.3 agent 기동 시 `Cannot open display` / Chromium `Page crashed!`

**Mac**:
- `Cannot open display` → `unset DISPLAY` 후 재시도 (XQuartz 잔재 env 때문)
- `Page crashed!` → macOS "확인 없이 열기" 권한 (시스템 설정 → 보안)

**Windows 11 / WSL2**:
- `Cannot open display` → WSLg 비활성. 관리자 PowerShell `wsl --update` → `wsl --shutdown` → 재기동. WSL 안에서 `echo $DISPLAY $WAYLAND_DISPLAY` 로 확인
- `Page crashed!` → `/dev/shm` 공유메모리 부족. `sudo mount -o remount,size=2G /dev/shm` (영구: `/etc/fstab` 에 `tmpfs /dev/shm tmpfs defaults,size=2g 0 0`)
- Playwright 의존 deb 누락:
  ```bash
  ~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install-deps chromium
  ```

### 4.4 Pipeline Stage 3 `Dify /v1/chat-messages` 400 또는 timeout

3 대 원인:

1. **호스트 Ollama 미기동** — `ollama list` / `curl http://127.0.0.1:11434/api/tags` 확인 후:
    - Mac: `brew services start ollama`
    - Windows: 트레이 앱 재기동 또는 `Start-Process ollama -ArgumentList 'serve'`
2. **모델 이름 불일치** — `OLLAMA_MODEL` env 값 vs `ollama list` NAME 비교. Chatflow yaml 의 모델명도 함께 확인 ([§3.7](#37-ollama-모델-관리-호스트))
3. **`--add-host` 누락** — `docker exec dscore.ttc.playwright curl -fsS http://host.docker.internal:11434/api/tags` 실패 시 docker run 에 `--add-host host.docker.internal:host-gateway` 재추가

### 4.5 Dify 가 옛 base_url 로 호출 (`Connection refused`)

provision.sh 재실행으로 credential swap + Redis FLUSH + 재기동 연쇄 자동 수행:

```bash
docker exec dscore.ttc.playwright bash /opt/provision.sh
```

완전 재프로비저닝:

```bash
docker exec dscore.ttc.playwright rm -f /data/.app_provisioned
docker restart dscore.ttc.playwright
```

### 4.6 agent-setup 이 `JDK 21 미설치` 로 중단

Mac:

```bash
brew install --cask temurin@21
# 또는
brew install openjdk@21
sudo ln -sfn /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk \
  /Library/Java/JavaVirtualMachines/openjdk-21.jdk
```

WSL2 Ubuntu:

```bash
sudo apt update && sudo apt install -y openjdk-21-jdk-headless
# 여러 JDK 가 있어 update-alternatives 가 다른 버전을 가리키면:
sudo update-alternatives --config java    # 21 선택
```

### 4.7 Playwright Chromium 설치 실패

Mac:

```bash
source ~/.dscore.ttc.playwright-agent/venv/bin/activate
python -m playwright install chromium --force
```

WSL2 Ubuntu:

```bash
~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install chromium --force
~/.dscore.ttc.playwright-agent/venv/bin/python -m playwright install-deps chromium
```

### 4.8 이미지가 너무 작다 (2-3GB)

아키텍처 불일치 시 qemu 크로스 빌드 silent-fail. `build.sh` 는 `uname -m` 자동 감지하지만 실수로 `TARGET_PLATFORM` override 했다면:

```bash
docker rmi dscore.ttc.playwright:latest
./build.sh         # 자동 감지
docker image inspect dscore.ttc.playwright:latest --format '{{.Architecture}}'
```

### 4.9 Pipeline 이 FAILURE 로 끝나지만 실제로는 스텝이 PASS 하는 것 같다

`run_log.jsonl` 을 보면 대부분 `status: PASS` 인데 마지막 FAIL 하나로 빌드 전체가 실패하는 경우. 흔한 원인:

- **시나리오가 step 1 (navigate) 없이 시작** → Guard 가 prepend 해 복구. 콘솔 `[Guard]` 로그 확인
- **Google.com 헤드리스 차단** → 시나리오 페이지가 `about:blank` / `/sorry/` 로 끝나 G-3 가드가 FAIL 처리. 검증은 [§1.4](#14-step-4-첫-pipeline-실행-검증) 의 네이버 시나리오 권장
- **LLM 이 selector 에 `role=searchbox` 처럼 `name=` 없는 단독 role 을 넣음** → executor 의 LocatorResolver 가 false-positive 방지로 거부 → LocalHealer 치유 시도 → 실패 시 FAIL. 대부분 치유 성공하지만 드물게 타임아웃
- **`[Retry N/3]` 후에도 "시나리오 구조 검증 실패"** → Dify 가 3 회 연속 무효 시나리오 반환 (배열 비거나 action 누락 등). Ollama 가 응답 중간에 끊기거나 프롬프트 시스템 변수가 깨진 케이스. 해결:
  1. `docker exec dscore.ttc.playwright tail -50 /data/logs/dify-api.log` 로 실제 Dify 내부 에러 확인
  2. `SRS_TEXT` 를 더 구체적이고 짧게 재작성 (LLM 이 혼란을 덜 겪음)
  3. 그래도 반복되면 [§3.7 모델 선택 기준](#37-ollama-모델-관리-호스트) 에서 `llama3.1:8b` / `qwen2.5:7b` 같은 더 큰 모델로 교체 — **최후 수단**

### 4.10 Google.com 대상 시나리오가 계속 실패

Google 의 봇 탐지가 Playwright Chromium 을 차단해 captcha / `/sorry/` 페이지로 리다이렉트. 해결책:

- **TARGET_URL 을 네이버 등으로 변경** (검증 시나리오의 표준) — [§1.4](#14-step-4-첫-pipeline-실행-검증)
- 또는 Chromium 을 headed + slow_mo 로 동작시키고 수동 captcha 해제

### 4.11 시계 오류로 Dify 로그인 `Invalid encrypted data`

Mac:

```bash
sudo sntp -sS time.apple.com
docker restart dscore.ttc.playwright
```

Windows 11 / WSL2:

```powershell
# 관리자 PowerShell
w32tm /resync
```

```bash
# WSL 안에서
sudo hwclock -s
docker restart dscore.ttc.playwright
```

### 4.12 Windows: WSL2 오프라인 설치 (폐쇄망)

온라인 머신에서 미리 수집:

1. **WSL 커널 MSI** — `https://learn.microsoft.com/windows/wsl/install-manual` 의 "WSL2 Linux 커널 업데이트 패키지"
2. **Ubuntu rootfs** — `https://cloud-images.ubuntu.com/wsl/jammy/current/` 의 `ubuntu-jammy-wsl-amd64-ubuntu.rootfs.tar.gz`
3. 본 번들 `dscore.ttc.playwright-*.tar.gz` + `playwright-allinone/` 폴더 전체 + `apt-get download` 로 수집한 의존 deb (openjdk-21, python3.12 등)

폐쇄망 Windows 11 관리자 PowerShell:

```powershell
# (1) WSL / VirtualMachinePlatform 기능 활성 — 재부팅
dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
Restart-Computer

# (2) WSL 커널 MSI 설치
msiexec /i D:\Usb\wsl_update_x64.msi /quiet

# (3) 기본 WSL 버전 2
wsl --set-default-version 2

# (4) Ubuntu tarball 을 import
mkdir C:\WSL\Ubuntu
wsl --import Ubuntu C:\WSL\Ubuntu D:\Usb\ubuntu-jammy-wsl-amd64-ubuntu.rootfs.tar.gz --version 2

# (5) 기본 사용자 설정
wsl -d Ubuntu -- bash -c "useradd -m -G sudo -s /bin/bash <사용자명> && passwd <사용자명>"
wsl --manage Ubuntu --set-default-user <사용자명>
```

이후 WSL Ubuntu 셸 진입 → 의존성 deb 로컬 설치 → 본 README [§1.3 Step 1](#13-분리-배포-빌드-머신과-실행-머신이-다른-경우) 부터 진행.

### 4.13 Windows 11: Ollama 가 CPU 로 추론 (1-3 tok/s)

```powershell
# GPU 로드 로그
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 100 | Select-String 'GPU|CUDA|offload'
# "offloaded N/N layers to GPU" 가 비(非)-0 이어야 GPU 사용

# VRAM 점유 확인
nvidia-smi
```

체크:

1. NVIDIA 드라이버 최신
2. `winget upgrade Ollama.Ollama`
3. 다른 프로세스 VRAM 점유 중이면 닫기
4. VRAM 용량 — `gemma4:26b` 는 ~17GB 소비, RTX 4090 / RTX 6000 24GB+ 권장. 경량 옵션은 `gemma4:e4b` (5-6GB, RTX 30/40 8GB+)

### 4.14 Windows 11: WSL2 메모리 부족 빌드 OOM

`%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
memory=16GB
processors=8
swap=8GB
```

관리자 PowerShell 에서 `wsl --shutdown` → WSL 재기동.

### 4.15 디스크 공간 부족으로 빌드 실패

`build.sh` 는 20GB+ 여유를 요구한다. 부족 시:

```bash
# 안전한 빌드 캐시 정리 (실행 중인 컨테이너 / 이미지 / 볼륨 영향 없음)
docker builder prune -a -f

# dangling 이미지 정리 (태그 없는 것만)
docker image prune -f

# 여전히 부족하면 — 주의: 사용 중이지 않은 이미지와 볼륨까지 삭제
docker system prune -a --volumes
```

---

## 부록 A. 토폴로지 / 볼륨 구조

### 프로세스 토폴로지

```
supervisord (PID 1, tini 위, 10 개 프로그램)
├─ postgresql (:5432)
├─ redis (:6379)
├─ qdrant (:6333)
├─ dify-plugin-daemon (:5002)
├─ dify-api (:5001)
├─ dify-worker / dify-worker-beat (Celery)
├─ dify-web (:3000)
├─ nginx (:18081 → dify-api / dify-web)
└─ jenkins (:18080, :50001 JNLP)

호스트 (Mac 또는 WSL2 Ubuntu)
├─ ollama (:11434)
│   ├─ Mac:          macOS 네이티브 — Metal 가속
│   └─ Windows 11:   Windows 네이티브 — CUDA 가속 (Docker Desktop 포워딩)
└─ jenkins agent (JDK 21, Node 레이블 mac-ui-tester)
   └─ Playwright Chromium (headed 또는 headless)
      ├─ Mac:        macOS 네이티브 창
      └─ Windows 11: WSLg 경유 Windows 데스크탑 창
```

### 볼륨 구조

컨테이너 볼륨 `dscore-data` → `/data/`:

```
/data/
├── .initialized               # seed 완료 플래그
├── .app_provisioned           # provision 완료 플래그
├── pg/                        # PostgreSQL 데이터
├── redis/                     # Redis AOF
├── qdrant/                    # Qdrant 벡터 스토어
├── jenkins/                   # JENKINS_HOME (plugins / jobs / credentials / nodes)
├── dify/                      # storage + plugins/packages
└── logs/                      # 서비스별 로그
```

호스트 agent 디렉토리 (Mac / WSL 공통 — `$HOME` 하위):

```
~/.dscore.ttc.playwright-agent/
├── venv/                      # Python 3.11+ + playwright
├── agent.jar                  # Jenkins remoting
├── run-agent.sh               # agent 기동 스크립트 (SCRIPTS_HOME export)
└── workspace/ZeroTouch-QA/
    └── .qa_home/
        ├── venv → (symlink)
        └── artifacts/
            ├── scenario.json        # Dify 가 생성 (+ Guard 가 prepend 한 step 1)
            ├── scenario.healed.json # 실행 중 치유된 selector 반영본
            ├── run_log.jsonl        # 스텝별 PASS/HEALED/FAIL
            ├── index.html           # HTML 리포트
            ├── final_state.png      # 마지막 페이지 스크린샷
            ├── error_final.png      # FAIL 시 스크린샷
            └── regression_test.py   # 성공 시 독립 회귀 테스트 코드
```

### 외부 포트 (호스트 관점)

| 포트 | 컨테이너 내부 | 역할 |
|------|---------------|------|
| 18080 | jenkins :18080 | Jenkins UI + REST |
| 18081 | nginx :18081 | Dify (web + api 프록시) |
| 50001 | jenkins :50001 | JNLP — 호스트 agent 접속용 |

### 외부 네트워크 도메인 (빌드 타임 화이트리스트)

빌드 시점에만 필요 — 런타임은 외부 접근 없음:

| 도메인 | 용도 |
|--------|------|
| `updates.jenkins.io`, `get.jenkins.io`, `mirrors.jenkins.io` | Jenkins 플러그인 |
| `marketplace.dify.ai` | Dify 플러그인 |
| `github.com`, `objects.githubusercontent.com` | jenkins-plugin-manager.jar, qdrant |
| `registry-1.docker.io`, `auth.docker.io` | Docker Hub |
| `pypi.org`, `files.pythonhosted.org` | Python 패키지 |
| `playwright.azureedge.net` | Chromium (빌드 + agent-setup 양쪽) |
| `apt.postgresql.org`, `deb.debian.org` | OS 패키지 |
