# playwright-allinone_REFERENCE

이 문서는 운영 중 정확한 값을 확인하기 위한 참조 문서다. 처음부터 따라 하는 문서가 아니다. 설치 절차는 [quickstart.md](quickstart.md), 반복 운영 절차는 [operations.md](operations.md), Recording UI 카드별 사용법은 [recording-ui.md](recording-ui.md) 를 본다.

## 1. 현재 구현 상태

2026-04-30 기준 구현 요약:

| 항목 | 값 |
| --- | --- |
| Jenkins base | `jenkins/jenkins:2.555.1-lts-jdk21` |
| Dify API/Web | `langgenius/dify-api:1.13.3`, `langgenius/dify-web:1.13.3` |
| Dify Plugin Daemon | `langgenius/dify-plugin-daemon:0.5.3-local` |
| 컨테이너 이름 | `dscore.ttc.playwright` |
| 데이터 볼륨 | `dscore-data` → `/data` |
| Recording bind mount | host recordings dir → `/recordings` |
| 기본 Ollama LLM (OS 분기) | Mac → `gemma4:26b` / WSL2·Linux·Windows → `qwen3.5:9b` |
| 기본 임베딩 모델 | `bge-m3:latest` (호스트 Ollama) |
| DSL | 14대 표준 액션 + 보조 액션 |
| 테스트 컬렉션 | 699건 (`python3 -m pytest --collect-only -q test`) |

자동 프로비저닝 결과:

- Dify 관리자 계정
- Ollama provider/plugin 등록
- `ZeroTouch QA Brain`
- `Test Planning Brain`
- KB 2개: `kb_project_info`, `kb_test_theory`
- Jenkins credential 2개: `dify-qa-api-token`, `dify-test-planning-api-token`
- Jenkins job: `ZeroTouch-QA`
- Jenkins node 1개: `mac-ui-tester` 또는 `wsl-ui-tester`

## 2. 포트와 URL

| 포트 | 위치 | 역할 |
| --- | --- | --- |
| 18080 | 컨테이너 Jenkins | Jenkins UI + REST |
| 18081 | 컨테이너 nginx | Dify web/API proxy + local fixtures |
| 18092 | 호스트 recording_service | Recording UI + API |
| 50001 | 컨테이너 Jenkins | JNLP agent 연결 |
| 11434 | 호스트 Ollama | LLM runtime |

기본 계정:

| 서비스 | URL | 계정 |
| --- | --- | --- |
| Jenkins | `http://localhost:18080` | `admin / password` |
| Dify | `http://localhost:18081` | `admin@example.com / Admin1234!` |
| Recording UI | `http://localhost:18092` | localhost daemon, 별도 계정 없음 |

## 3. 프로세스 토폴로지

컨테이너:

```text
supervisord
├─ postgresql (:5432)
├─ redis (:6379)
├─ qdrant (:6333)
├─ dify-plugin-daemon (:5002)
├─ dify-api (:5001)
├─ dify-worker / dify-worker-beat
├─ dify-web (:3000)
├─ nginx (:18081)
└─ jenkins (:18080, :50001)
```

호스트:

```text
ollama (:11434)
jenkins agent (mac-ui-tester 또는 wsl-ui-tester)
└─ Playwright Chromium
recording_service (:18092)
```

컨테이너 안에는 Ollama와 Jenkins agent가 없다.

## 4. 데이터 위치

Docker volume `dscore-data`:

```text
/data/
├── .initialized
├── .app_provisioned
├── pg/
├── redis/
├── qdrant/
├── jenkins/
├── dify/
└── logs/
```

호스트 agent 디렉토리:

```text
~/.dscore.ttc.playwright-agent/
├── venv/
├── agent.jar
├── run-agent.sh
├── run-recording-service.sh
├── recording-service.pid
├── recording-service.log
├── recordings/
└── workspace/ZeroTouch-QA/
    └── .qa_home/
        ├── venv -> ~/.dscore.ttc.playwright-agent/venv
        └── artifacts/
```

Pipeline artifact 예:

```text
scenario.json
scenario.healed.json
run_log.jsonl
index.html
final_state.png
error_final.png
regression_test.py
llm_calls.jsonl
llm_sla.json
pytest_integration.xml
pytest_native.xml
```

Recording UI session 예:

```text
~/.dscore.ttc.playwright-agent/recordings/<sid>/
├── metadata.json
├── original.py
├── scenario.json
├── run_log.jsonl
├── regression_test.py
└── play-llm.log
```

모니터링 PC (Replay UI, 1회 설치형) 디렉토리:

```text
~/.dscore.ttc.monitor/
├── venv/
├── chromium/             ← PLAYWRIGHT_BROWSERS_PATH
├── auth-profiles/        ← Replay UI 와 Recording UI 가 공유 가능
├── scenarios/            ← .py 시나리오 업로드 위치
├── scripts/
├── runs/                 ← 실행 결과 (trace.zip, 보고서, 로그)
├── replay-ui.pid
└── replay-ui.stdout.log
```

휴대용 zip 풀린 디렉토리 (Replay UI portable):

```text
DSCORE-ReplayUI-portable-<os>-<ts>/
├── Launch-ReplayUI.bat            ← Windows: 더블클릭
├── Launch-ReplayUI.command        ← macOS: 더블클릭
├── Stop-ReplayUI.*
├── README.txt
├── embedded-python/  (Windows)    ← 또는 python/ (macOS)
├── site-packages/                 ← fastapi · uvicorn · playwright · pywin32 ...
├── chromium/                      ← Playwright Chromium 동봉
├── replay_service/  monitor/                ← Replay UI 소스 (zip 안에서는 평평)
├── recording_shared/  zero_touch_qa/        ← 공용 코드 카피
├── data/
│   ├── auth-profiles/  scenarios/  scripts/  runs/
└── .pack-stamp                    ← 자산 source SHA (자동 갱신 stamp)
```

## 5. 주요 스크립트

| 파일 | 사용자가 실행? | 역할 |
| --- | --- | --- |
| `build.sh` | 예 | 이미지 빌드, tarball 생성, 선택적으로 redeploy |
| `mac-agent-setup.sh` | 예 | macOS 호스트 agent + Recording UI 준비 |
| `wsl-agent-setup.sh` | 예 | WSL2 호스트 agent + Recording UI 준비 |
| `recording-ui/run-recording-ui.sh` | 예 | Recording UI 데몬 독립 운영 (18092) |
| `replay-ui/run-replay-ui.sh` | 예 | Replay UI 데몬 독립 운영 (18094, 모니터링 PC) |
| `monitor-build/build-monitor-runtime.sh` | 예 | 모니터링 PC 용 1회-설치형 zip 빌드 |
| `monitor-build/install-monitor.{sh,ps1,cmd}` | 예 | 모니터링 PC 설치 (1회) |
| `replay-ui-portable-build/pack-windows.ps1` | 예 | Replay UI 휴대용 zip 빌드 (Windows) |
| `replay-ui-portable-build/pack-macos.sh` | 예 | Replay UI 휴대용 zip 빌드 (macOS arm64) |
| `backup-volume.sh` | 예 | `dscore-data` 백업 |
| `restore-volume.sh` | 예 | `dscore-data` 복원 |
| `entrypoint.sh` | 아니오 | 컨테이너 PID 1, seed/provision/supervisord |
| `provision.sh` | 보통 아니오 | Dify/Jenkins 앱, credential, job, node 생성 |
| `pg-init.sh` | 아니오 | 빌드 타임 PostgreSQL seed 생성 |
| `nginx.conf` | 아니오 | Dify web/API proxy와 fixture 서빙 |
| `supervisord.conf` | 아니오 | 컨테이너 내부 프로세스 관리 |

## 6. 주요 환경변수

빌드/컨테이너:

| 이름 | 기본값 | 의미 |
| --- | --- | --- |
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | Docker image tag |
| `CONTAINER_NAME` | `dscore.ttc.playwright` | 컨테이너 이름 |
| `DATA_VOLUME` | `dscore-data` | Docker volume |
| `TARGET_PLATFORM` | 자동 | `linux/arm64` 또는 `linux/amd64` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | 컨테이너에서 볼 Ollama URL |
| `OLLAMA_MODEL` | OS 별 자동 (Mac=`gemma4:26b`, WSL2/Linux/Windows=`qwen3.5:9b`) | Dify provider 등록 LLM. chatflow YAML 의 placeholder 도 import 시 이 값으로 자동 치환 |
| `EMBEDDING_MODEL` | `bge-m3:latest` | Dify provider 등록 임베딩 (Test Planning RAG KB 용) |
| `AGENT_NAME` | 플랫폼별 | `mac-ui-tester` 또는 `wsl-ui-tester` |
| `DIFY_PUBLIC_URL` | `http://localhost:18081` | Dify public/share URL |

호스트 agent:

| 이름 | 기본값 | 의미 |
| --- | --- | --- |
| `NODE_SECRET` | docker logs에서 자동 추출 | Jenkins JNLP secret |
| `JENKINS_URL` | `http://localhost:18080` | Jenkins URL |
| `AUTO_INSTALL_DEPS` | `false` | JDK/Python 자동 설치 시도 |
| `MAC_AGENT_WORKDIR` | `~/.dscore.ttc.playwright-agent` | Mac agent dir |
| `WSL_AGENT_WORKDIR` | `~/.dscore.ttc.playwright-agent` | WSL agent dir |
| `FORCE_AGENT_DOWNLOAD` | `false` | agent.jar 재다운로드 |

Recording UI:

| 이름 | 기본값 | 의미 |
| --- | --- | --- |
| `RECORDING_PORT` | `18092` | Recording UI 포트 |
| `RECORDING_HOST` | `127.0.0.1` | bind host |
| `RECORDING_PYTHON` | `python3` | daemon 실행 Python |
| `RECORDING_HOST_ROOT` | `~/.dscore.ttc.playwright-agent/recordings` | session 저장소 |
| `RECORDING_CONTAINER_ROOT` | `/recordings` | 컨테이너에서 보는 session root |
| `RECORDING_CONTAINER_NAME` | `dscore.ttc.playwright` | 변환에 사용할 컨테이너 |

## 7. DSL 계약

표준 14대 액션:

```text
navigate
click
fill
press
select
check
hover
wait
verify
upload
drag
scroll
mock_status
mock_data
```

보조 액션:

```text
auth_login
reset_state
```

검증 원칙:

- scenario는 비어 있지 않은 JSON list여야 한다.
- 모든 step은 dict여야 한다.
- action은 표준 액션 또는 허용 보조 액션이어야 한다.
- `navigate`, `wait`, `press`, `reset_state` 외에는 target이 필요하다.
- `scroll.value`는 `into_view` 계열만 허용한다.
- `mock_status.value`는 정수 HTTP status여야 한다.
- `mock_data.value`는 비어 있으면 안 된다.

## 8. Recording Service API

주요 URL:

| Method | Path | 역할 |
| --- | --- | --- |
| `GET` | `/` | Recording UI HTML |
| `GET` | `/healthz` | health check |
| `POST` | `/recording/start` | Playwright codegen 시작 |
| `POST` | `/recording/stop/{sid}` | codegen 종료 + convert |
| `GET` | `/recording/sessions` | 세션 목록 |
| `GET` | `/recording/sessions/{sid}` | 세션 상세 |
| `GET` | `/recording/sessions/{sid}/scenario` | DSL JSON |
| `GET` | `/recording/sessions/{sid}/original` | 원본 Playwright Python |
| `POST` | `/recording/sessions/{sid}/assertion` | verify/mock/scroll/hover 보충 |
| `DELETE` | `/recording/sessions/{sid}` | 세션 삭제 |
| `POST` | `/experimental/sessions/{sid}/play-codegen` | 원본 테스트 코드 replay |
| `POST` | `/experimental/sessions/{sid}/play-llm` | DSL executor replay |
| `POST` | `/experimental/sessions/{sid}/enrich` | 코드 기반 문서 생성 |
| `POST` | `/experimental/sessions/{sid}/compare` | doc DSL과 비교 |

R-Plus API는 `/experimental/*` prefix를 유지하지만 별도 feature gate 없이 활성화되어 있다. UI에서는 세션 상태가 `done`일 때만 노출된다.

## 9. 빌드 타임 외부 접근

폐쇄망 반입 전 온라인 빌드 머신에서 접근이 필요한 주요 도메인:

| 도메인 | 용도 |
| --- | --- |
| `registry-1.docker.io`, `auth.docker.io` | Docker base image |
| `updates.jenkins.io`, `get.jenkins.io`, `mirrors.jenkins.io` | Jenkins plugins |
| `marketplace.dify.ai` | Dify plugin |
| `github.com`, `objects.githubusercontent.com` | qdrant, utility artifacts |
| `pypi.org`, `files.pythonhosted.org` | Python packages |
| `playwright.azureedge.net` | Chromium |
| `apt.postgresql.org`, `deb.debian.org` | OS packages |

런타임은 기본적으로 외부 인터넷 없이 동작하도록 설계되어 있다. 단, 호스트 Ollama 모델과 agent 런타임은 사전에 준비되어 있어야 한다.
