# TTC — Airgap Test Toolchain

이 저장소는 폐쇄망 반출용 **독립 All-in-One 패키지 2종**을 담고 있습니다.
각 하위 폴더는 자체 완결 배포 단위이며, **해당 폴더만 별도로 전달해도 빌드·구동이 가능**하도록 구성되어 있습니다.

## 구성

| 패키지 | 폴더 | 현재 구현 범위 | 실행 형태 |
|--------|------|----------------|-----------|
| **Playwright Zero-Touch QA** | [playwright-allinone/](playwright-allinone/) | 자연어/문서/녹화 스크립트 기반 E2E 시나리오 생성, Playwright 실행, 자가 치유, HTML 리포트, 회귀 스크립트 생성 | **하이브리드**: Jenkins controller + Dify + DB 는 컨테이너, Ollama 와 Jenkins agent/Chromium 은 호스트 |
| **Code & AI Quality** | [code-AI-quality-allinone/](code-AI-quality-allinone/) | Jenkins 5개 Job 기반 코드 사전학습, SonarQube 정적분석, Dify 기반 이슈 분석/등록, Golden Dataset 기반 AI 평가 | **통합 스택**: all-in-one 컨테이너 + GitLab compose 스택, 호스트 Ollama 연동 |

두 패키지는 포트/볼륨/네트워크를 분리해 설계되어 있으며, 같은 호스트에 함께 올리는 것을 전제로 작성돼 있습니다. 상세 포트, 운영 순서, 장애 대응은 각 폴더의 `README.md` 를 기준 문서로 봐야 합니다.

## 저장소 구조

```text
airgap-test-toolchain/
├── README.md
├── playwright-allinone/
│   ├── README.md
│   ├── Dockerfile
│   ├── build.sh
│   ├── mac-agent-setup.sh
│   ├── wsl-agent-setup.sh
│   ├── zero_touch_qa/
│   └── test/
└── code-AI-quality-allinone/
    ├── README.md
    ├── Dockerfile
    ├── docker-compose.mac.yaml
    ├── docker-compose.wsl2.yaml
    ├── scripts/
    ├── jenkinsfiles/
    ├── pipeline-scripts/
    └── eval_runner/
```

## 빌드 및 구동 요약

루트가 아니라 **각 하위 폴더로 이동해서** 작업합니다.  
아래는 "어떤 플랫폼에서 어떤 스크립트를 먼저 실행하는지"를 빠르게 판단하기 위한 요약이며, 실제 운영 파라미터와 트러블슈팅은 각 하위 README를 따라가야 합니다.

### 1. Playwright Zero-Touch QA

#### macOS

```bash
cd playwright-allinone
bash build.sh --redeploy          # 빌드 + 컨테이너 재배포 + 호스트 agent 연결까지 one-shot
```

- 컨테이너에는 Jenkins controller, Dify, PostgreSQL, Redis, Qdrant, nginx 가 올라갑니다.
- 호스트에는 Ollama 와 Jenkins agent/Playwright Chromium 이 올라갑니다.
- `--redeploy` 없이 `bash build.sh` 만 실행하면 이미지 빌드만 수행합니다.
- agent 수동 연결이 필요하면 `mac-agent-setup.sh` 를 사용합니다.

#### Windows 11 + WSL2

```bash
cd playwright-allinone
bash build.sh --redeploy
```

- Ollama 는 Windows 호스트에, Jenkins agent 는 WSL2 Ubuntu 안에 올라가는 하이브리드 구조입니다.
- headed Chromium 은 WSLg 를 통해 Windows 데스크탑에 표시됩니다.
- agent 수동 연결이 필요하면 `wsl-agent-setup.sh` 를 사용합니다.

### 2. Code & AI Quality

#### macOS

```bash
cd code-AI-quality-allinone
bash scripts/download-plugins.sh   # 최초 1회, 온라인 환경 필요
bash scripts/build-mac.sh
bash scripts/run-mac.sh
```

- `download-plugins.sh` 는 Jenkins/Dify 플러그인 seed 를 준비합니다.
- `build-mac.sh` 는 all-in-one 이미지와 관련 자산을 빌드합니다.
- `run-mac.sh` 는 mac용 docker compose 스택을 기동합니다.
- GitLab 은 compose 스택에 포함되고, LLM 추론은 호스트 Ollama 를 사용합니다.

#### Windows 11 + WSL2

```bash
cd code-AI-quality-allinone
bash scripts/download-plugins.sh   # 최초 1회, 온라인 환경 필요
bash scripts/build-wsl2.sh
bash scripts/run-wsl2.sh
```

- `build-wsl2.sh` 는 WSL2/Docker Desktop 기준 이미지를 빌드합니다.
- `run-wsl2.sh` 는 WSL2용 compose 스택을 기동합니다.
- 운영 시 Jenkins, Dify, SonarQube, PostgreSQL, Redis, Qdrant, GitLab 이 함께 올라갑니다.

### 3. 오프라인 반출/복원 흐름

- `playwright-allinone`: `build.sh` 로 이미지 tarball 생성 후 대상 머신에서 `docker load` + `docker run`, 이후 호스트 agent 연결
- `code-AI-quality-allinone`: 온라인 준비 머신에서 플러그인/이미지/모델 반출 자산 준비 후, 대상 머신에서 `offline-load.sh` + `run-*.sh`

## 첫 진입점

- Playwright 자동화 시나리오 생성/실행이 목적이면: [playwright-allinone/README.md](playwright-allinone/README.md)
- 코드 품질 체인 + AI 평가 파이프라인 운영이 목적이면: [code-AI-quality-allinone/README.md](code-AI-quality-allinone/README.md)
- 하위 폴더 문서는 설치 절차, 포트, 운영, 복구, 오프라인 배포 순서를 포함한 기준 문서입니다.

## 패키지별 요약

### 1. Playwright Zero-Touch QA

- 핵심 엔진은 `zero_touch_qa/` 패키지입니다.
- 실행 모드는 `chat`, `doc`, `convert`, `execute` 네 가지입니다.
- `chat`: 자연어 요구사항(`SRS_TEXT`)을 입력으로 받아 Dify가 테스트 시나리오를 생성하고 실행합니다.
- `doc`: 기획서/PDF 같은 문서에서 텍스트를 추출해 그 내용을 바탕으로 테스트 시나리오를 생성하고 실행합니다.
- `convert`: Playwright 녹화 스크립트(`.py`)를 내부 DSL 시나리오로 변환합니다.
- `execute`: 이미 존재하는 `scenario.json` 파일을 그대로 재실행합니다.
- 시나리오 실행 시 locator fallback, action alternative, 로컬 DOM healing, Dify healing, 검색 전용 휴리스틱까지 포함한 다단계 자가 치유를 수행합니다.
- 실제 브라우저 창을 띄우는 headed Playwright 실행을 위해 호스트 Jenkins agent 구성이 필요합니다.

상세 설치/운영 가이드는 [playwright-allinone/README.md](playwright-allinone/README.md) 를 참고하세요.

### 2. Code & AI Quality

- 현재 코드 기준으로 Jenkins Job 은 `00 코드 분석 체인`, `01 코드 사전학습`, `02 코드 정적분석`, `03 코드 정적분석 결과분석 및 이슈등록`, `04 AI평가` 까지 포함합니다.
- `00 코드 분석 체인`: 하나의 커밋 기준으로 `01 → 02 → 03`을 순차 실행하고 결과를 집계하는 오케스트레이션 Job 입니다.
- `01 코드 사전학습`: 대상 저장소를 AST 청킹하고 컨텍스트를 구축해 Dify Knowledge Base 쪽 입력 자산을 만드는 단계입니다.
- `02 코드 정적분석`: SonarQube 스캔을 수행해 정적분석 결과를 생성하는 단계입니다.
- `03 코드 정적분석 결과분석 및 이슈등록`: Sonar 이슈를 export한 뒤 Dify로 분석하고 GitLab Issue로 등록하는 단계입니다.
- `04 AI평가`: Golden Dataset을 사용해 대상 AI를 평가하고 `summary.json`, `summary.html` 리포트를 생성하는 단계입니다.
- 런타임은 Dify, Jenkins, PostgreSQL, Redis, Qdrant, SonarQube, GitLab 조합이며, LLM 추론은 호스트 Ollama 를 사용합니다.

상세 설치/운영 가이드는 [code-AI-quality-allinone/README.md](code-AI-quality-allinone/README.md) 를 참고하세요.

## 문서 사용 원칙

- 루트 `README.md`: 저장소 개요, 폴더 역할, 진입 경로 안내
- `playwright-allinone/README.md`: Zero-Touch QA 이미지의 설치/실행/운영 기준 문서
- `code-AI-quality-allinone/README.md`: Code & AI Quality 스택의 설치/실행/운영 기준 문서

즉, **실제 배포와 운영 판단은 항상 각 하위 폴더의 README를 우선**해야 합니다.
