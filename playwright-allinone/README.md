# playwright-allinone — 문서 입구

**Zero-Touch QA All-in-One** 은 테스트 자동화에 필요한 Jenkins / Dify / DB 를 한 docker 컨테이너로 올리고, Ollama 와 실제 브라우저는 사용자 PC 에서 처리하는 배포본이다.

> **이 문서가 처음이면** — [docs/quickstart.md](docs/quickstart.md) 한 페이지만 따라가면 빌드 → 컨테이너 기동 → 첫 녹화·재생까지 한 번 성공할 수 있다.

## 시스템 한눈에

```text
┌─────────────────────────── docker 컨테이너 ───────────────────────────┐
│  Jenkins (18080)   Dify (18081)   PostgreSQL · Redis · Qdrant · nginx │
└────────────────────────────────────────────────────────────────────────┘
                                  ↕ HTTP
┌─────────────────────────────── 호스트 PC ────────────────────────────┐
│  Ollama        Jenkins agent       Recording UI (18092)               │
│  (LLM 추론)    (실제 Chromium)     (녹화·재생 GUI 서비스)             │
└────────────────────────────────────────────────────────────────────────┘
```

| 위치 | 무엇이 도는가 | 왜 |
| --- | --- | --- |
| 컨테이너 | Jenkins · Dify · DB · nginx | 서버 요소를 한 번에 |
| 호스트 | Ollama · Jenkins agent · Playwright Chromium · Recording UI | GPU + 실제 데스크탑 브라우저 창이 필요 |

## 두 가지 사용 경로

| 경로 | 어떨 때 | 입구 |
| --- | --- | --- |
| **Jenkins Pipeline** | 자연어 요구사항 → LLM 이 시나리오 작성 → 자동 실행 | <http://localhost:18080> (admin/password) |
| **Recording UI** | 사용자가 직접 브라우저 조작을 녹화 → DSL 시나리오 → 재생·치유 | <http://localhost:18092> |

두 경로는 같은 14대 DSL 시나리오 형식을 공유하므로 한쪽에서 만든 결과를 다른 쪽이 그대로 받을 수 있다.

## 모니터링 PC 로 시나리오 옮기기 (Replay UI)

녹화 PC 에서 만든 시나리오를 다른 PC 들에서 자동 실행하고 싶을 때:

1. **녹화 PC** — Recording UI 의 결과 카드 → `Original Script` 또는 `셀프힐링 후` 카드의 `⬇ 다운로드` 클릭. 받는 `.py` 는 평문 자격증명 자동 sanitize (`auth_flow.sanitize_script`) 통과한 안전한 본문.
2. **모니터링 PC (1회 설치)** — `monitor-runtime-<날짜시각>.zip` 을 풀고 `install-monitor` 한 번 실행 (Mac/Linux = `bash install-monitor.sh`, Windows = `install-monitor.ps1`; Windows 는 실행까지 자동).
3. **모니터링 PC (사용)** — <http://127.0.0.1:18094> 에서 (a) 로그인 프로파일 등록 (필요 시 — 비로그인 시나리오면 생략), (b) `시나리오 스크립트` 카드에 받아온 `.py` 업로드, (c) 적용할 프로파일 select (또는 *비로그인*) + verify URL 옵션 → `▶ 실행` → 스텝별 스크린샷 + HTML 리포트.

### monitor-runtime zip 만드는 법 (빌드 머신)

`monitor-runtime-<날짜시각>.zip` 은 빌드 머신 (Mac/Linux/WSL2/Git Bash) 에서 다음 중 하나로 만든다.

```bash
# 모니터링 PC 패키지만:
bash monitor-build/build-monitor-runtime.sh

# 녹화 PC tarball + 모니터링 PC zip 을 한 번에:
bash ../export-airgap.sh
```

옵션: `--target win64` / `--target macos-arm64` (둘 중 한 OS 만 담아 zip 작게), `--no-chromium` (이미 받은 Chromium 재활용), `--reuse-cache`.

산출되는 zip 의 특성:

| 항목 | 내용 |
|---|---|
| 동봉되는 것 | Windows Python 3.11 installer, Python wheels (양 OS), Playwright Chromium (양 OS), 소스 모듈, 설치 스크립트 (`.sh` + `.ps1`) |
| 대상 PC 인터넷 요구 | **없음** — pip 는 `--no-index --find-links wheels/<os>/`, Chromium 은 오프라인 복사 |
| 대상 PC 의존성 | Windows 는 Python 선행 설치 불필요. Mac/Linux 는 Python 3.11.x 필요. monitor-runtime wheel 번들은 cp311 전용 |
| 운반 매체 | USB / 외장 디스크 / 사내 공유 폴더 등 자유. zip 한 개 파일만 옮기면 됨 |
| 멀티 OS 한 장 | win64 + macos-arm64 가 한 zip 안에 같이 들어가서, 같은 zip 으로 Mac · Windows 어디서나 설치 (대상 PC 가 자기 OS 분만 골라 씀) |

대상 PC 에서 **한 번 설치한 USB 를 다른 PC 에 그대로 꽂아 실행** 은 venv 의 절대경로 의존 때문에 안 된다. 항상 대상 PC 에서 한 번씩 `install-monitor` 를 새로 돌려 그 PC 의 로컬 디스크에 venv 가 생성된다.

| 도구 | 위치 |
| --- | --- |
| Replay UI | <http://127.0.0.1:18094> (모니터링 PC 자기 자신만 접속, LAN 노출 X) |
| 단일 진입점 launcher | `./run-replay-ui.sh {start\|stop\|restart\|status\|logs\|foreground\|doctor}` |
| CLI 실행 | `python -m monitor replay-script <script.py> --out <결과 폴더> [--profile <alias>] [--verify-url <URL>]` |
| CLI 로그인 등록 | `python -m monitor profile seed <프로파일이름> --target <사이트 URL>` |

> 2026-05-11 D17 일원화 — 이전 `bundle.zip` 흐름은 폐기됐다. 한 시나리오 = 한 `.py`. 적용할 로그인 프로파일과 verify URL 은 받는 쪽 Replay UI 에서 사용자가 명시 (또는 *비로그인* 으로 비워둠). 자세한 결정은 [PLAN_AUTH_PROFILE_NAVER_OAUTH.md §2 D17](docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md#2-의사결정-로그).

처음 보는 사용자가 따라할 수 있게 단계별로 정리한 문서는 [docs/replay-ui-guide.md](docs/replay-ui-guide.md). 통합 테스트 매트릭스는 [docs/replay-ui-integration-tests.md](docs/replay-ui-integration-tests.md). 설계 결정 배경은 `.claude/plans/squishy-wishing-emerson.md`.

## 가장 짧은 시작

```bash
cd playwright-allinone
chmod +x *.sh
./build.sh --redeploy
```

이 명령은 image 빌드 + 컨테이너 기동 + 호스트 agent 연결까지 한 번에 한다 (첫 빌드 30~90분, 이후 3~10분). 완료 후 브라우저로 다음에 접속:

| 서비스 | URL | 기본 계정 |
| --- | --- | --- |
| Jenkins | <http://localhost:18080> | admin / password |
| Dify | <http://localhost:18081> | `admin@example.com` / `Admin1234!` |
| Recording UI | <http://localhost:18092> | (로컬 서비스, 계정 없음) |

자세한 설치 절차 (사전 요구사항, agent 자동/수동 연결, 첫 Pipeline 실행, 첫 녹화·재생) 는 [QUICKSTART](docs/quickstart.md) 에 단계별로 있다.

## build.sh 자세히

이 스크립트 한 개가 image 빌드 → 컨테이너 기동 → 데이터 초기화 → host agent 연결까지 모두 처리한다. 옵션 조합으로 "지금 무엇까지 할지" 를 선택한다.

### 어디서 실행하나

어디서 실행하든 `./build.sh --redeploy` 한 번이면 빌드 → 컨테이너 → provision → agent 까지 자동 완료.

| 호스트 | build.sh 실행 위치 | 분기 결과 |
|---|---|---|
| Mac | macOS 터미널 (`zsh` / `bash`) | `uname -s = Darwin` → `gemma4:26b` + `mac-ui-tester` + `mac-agent-setup.sh` 자동 기동 |
| Windows | **WSL2 Ubuntu 안** | `uname -s = Linux` → `qwen3.5:9b` + `wsl-ui-tester` + `wsl-agent-setup.sh` 자동 기동 |

agent 실행 로그는 `/tmp/dscore-agent.log`.

### 사전 요구사항

- Docker 26+ (buildx 활성), 디스크 20GB 이상 여유
- 호스트에 Ollama 가 설치·기동되어 있고 다음 모델이 pull 되어 있어야 한다.

| 호스트 | LLM | 임베딩 |
|---|---|---|
| Mac | `ollama pull gemma4:26b` | `ollama pull bge-m3` |
| WSL2 / Windows | `ollama pull qwen3.5:9b` | `ollama pull bge-m3` |

호스트 OS 는 `build.sh` 가 `uname -s` 로 자동 판별해 LLM 기본값을 잡는다 (Mac=`gemma4:26b`, 그 외=`qwen3.5:9b`). 임베딩은 양쪽 모두 `bge-m3:latest`.

### 옵션

| 옵션 | 역할 | 자주 쓰는 상황 |
|---|---|---|
| (없음) | 빌드만 — `dscore.ttc.playwright-<ts>.tar.gz` 산출 | airgap 머신으로 옮길 배포 패키지 만들 때 |
| `--redeploy` | 빌드 + 기존 컨테이너 swap + 호스트 agent 재연결. 데이터 볼륨은 보존. | 같은 머신에서 코드 수정 후 즉시 재기동 |
| `--redeploy --reprovision` | 위 + provision 재실행. KB 임베딩 / Jenkins 이력 / 챗봇 대화는 보존하되 chatflow / Jenkins job 정의 / Dify provider 등록은 새 이미지 기준으로 재생성. | chatflow YAML 이나 provision 로직을 바꿨을 때 |
| `--redeploy --fresh` | 위 + 볼륨까지 삭제 (`dscore-data` 제거). **모든 데이터 폐기**. | 처음부터 다시. 디버깅 막판. |
| `--redeploy --no-agent` | 빌드 + 컨테이너만 재기동, agent 연결은 스킵 | CI 머신에서 컨테이너만 갱신 |
| `-h`, `--help` | 도움말 출력 | |

`--fresh` 와 `--reprovision` 동시 지정 시 `--fresh` 가 우선 (어차피 전체 wipe).

### 환경변수

| 변수 | 기본값 | 의미 |
|---|---|---|
| `OLLAMA_MODEL` | OS 별 자동 (Mac=`gemma4:26b`, WSL2/Linux/Windows=`qwen3.5:9b`) | Dify provider 에 등록될 LLM. chatflow YAML 의 placeholder 도 import 시 이 값으로 자동 치환. |
| `EMBEDDING_MODEL` | `bge-m3:latest` | Test Planning RAG KB 용 임베딩 모델. |
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | Docker image 태그 |
| `TARGET_PLATFORM` | `uname -m` 자동 감지 | `linux/arm64` (Apple Silicon) 또는 `linux/amd64`. 다른 아키 서버로 배포할 때만 override. |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<ts>.tar.gz` | 산출 tar 파일명 |
| `FORCE_PLUGIN_DOWNLOAD` | `false` | `true` 면 `jenkins-plugins/` `dify-plugins/` 에 파일이 있어도 재다운로드. 플러그인 버전 갱신 시만. |
| `AGENT_NAME` | OS 별 자동 (Mac=`mac-ui-tester`, 그 외=`wsl-ui-tester`) | Jenkins Node 이름 |
| `RECORDING_HOST_ROOT` | `~/.dscore.ttc.playwright-agent/recordings` | host 의 녹화 디렉토리 (컨테이너 `/recordings` 로 bind) |

### 시나리오별 명령

```bash
# 처음 한 번 — 빌드 + 컨테이너 + agent 까지 (15-30분 첫 빌드, 이후 3-10분)
./build.sh --redeploy

# tar.gz 만 만들어 다른 머신으로 전달 (airgap)
./build.sh

# 코드/스크립트만 수정 → 같은 머신에서 즉시 갱신
./build.sh --redeploy

# chatflow YAML 또는 provision 로직 수정 → 데이터 보존하며 재 provision
./build.sh --redeploy --reprovision

# 처음부터 다시 (모든 데이터 폐기)
./build.sh --redeploy --fresh

# WSL2 호스트에서 qwen3.5:9b 대신 다른 모델로 강제
OLLAMA_MODEL=qwen3-coder:30b ./build.sh --redeploy --reprovision

# CI 환경 — agent 연결 없이 컨테이너만 갱신
./build.sh --redeploy --no-agent

# 플러그인 버전 올림 — hpi/difypkg 강제 재다운로드
FORCE_PLUGIN_DOWNLOAD=true ./build.sh
```

### 산출물

| 위치 | 무엇 |
|---|---|
| `./dscore.ttc.playwright-<timestamp>.tar.gz` | image 압축본 (airgap 배포용, ~5-7GB) |
| `dscore.ttc.playwright:latest` (docker image) | 로컬 image |
| `dscore.ttc.playwright` (docker container) | 실행 중인 컨테이너 (`--redeploy` 시) |
| `dscore-data` (docker volume) | DB / Jenkins 이력 / Dify KB 데이터. `--fresh` 가 아니면 보존. |

### 자주 막히는 곳

- **호스트에 모델이 없어서 Dify 가 응답 못 함** → `ollama list` 로 LLM·임베딩 두 개가 다 있는지 확인.
- **`--reprovision` 인데 변경이 반영 안 된 것 같다** → 컨테이너 안 `/data/.app_provisioned` 마커가 정상 wipe 됐는지: 컨테이너 entrypoint 로그에 `앱 프로비저닝 시작` 이 찍혀야 한다.
- **빌드는 됐는데 첫 Pipeline 호출이 timeout** → 호스트 Ollama 가 첫 모델 로드 중일 가능성. 한 번 `curl http://localhost:11434/api/generate -d '{"model":"qwen3.5:9b","prompt":"hi"}'` 으로 워밍업 후 재시도.

상세 결함·수정 이력은 [docs/wsl2-build-fixes-2026-05-06.md](docs/wsl2-build-fixes-2026-05-06.md).

## 문서 안내

| 문서 | 언제 본다 |
| --- | --- |
| [docs/quickstart.md](docs/quickstart.md) | **처음 한 번 성공시키기** — 사전 요구사항, 빌드, 기동 확인, 첫 Pipeline, 첫 녹화·재생 |
| [docs/recording-ui.md](docs/recording-ui.md) | Recording UI 의 6개 카드 (Login Profile / Discover / Recording / Play / 결과 / 세션) 카드별 사용법 |
| [docs/operations.md](docs/operations.md) | 재배포 / 백업·복원 / 모델 변경 / Recording UI 서비스 재기동 / 장애 대응 |
| [docs/replay-ui-guide.md](docs/replay-ui-guide.md) | **모니터링 PC 운영자 / 테스터 용 — Replay UI 설치 + 사용 가이드** |
| [docs/replay-ui-integration-tests.md](docs/replay-ui-integration-tests.md) | Replay UI · 모니터링 PC bundle 흐름 통합 테스트 매트릭스 (자동 22 + 수동 20) |
| [docs/reference.md](docs/reference.md) | 포트 / 볼륨 / 환경변수 / 데이터 구조 / DSL 액션 14종 / API 계약 |
| [docs/recording-troubleshooting.md](docs/recording-troubleshooting.md) | 자주 발생하는 녹화·재생 에러 모음 |
| [docs/](docs/) | 결정 문서 (PLAN_*.md) — 설계 배경 / 트레이드오프 / 검증 |

## Recording UI 의 핵심 능력 (요약)

자세한 건 [RECORDING_UI 문서](docs/recording-ui.md) 에. 요약하면:

- **🔐 Login Profile** — 테스트 대상의 로그인 세션을 한 번 시드 → 매 녹화·재생마다 재사용.
- **🔍 Discover URLs** — 사이트 URL 자동 수집 → tour 시나리오 자동 생성.
- **🎬 Recording** — Playwright codegen 으로 사용자 조작 녹화 → 14-DSL 변환.
- **▶️ Play & more** — 두 가지 재생 모드 (codegen 자가 치유 / Dify LLM 치유).
- **자동 정리** — 중복 click 압축, IME 노이즈 키 제거 (CapsLock/Unidentified), popup race fallback, transient alert skip 등 codegen 부산물을 자동으로 정리해 시나리오가 깨지지 않게 한다.

## 자주 묻는 것

**Q. Jenkins 와 Recording UI 중 어느 쪽을 써야 하나?**
요구사항이 글로 잘 정의되어 있으면 Jenkins (LLM 이 시나리오 작성). 화면을 직접 보면서 클릭으로 정의하는 게 빠르면 Recording UI. 둘은 같은 시나리오 포맷을 공유.

**Q. 호스트 Ollama 가 꼭 필요한가?**
Jenkins Pipeline 의 LLM 단계 (시나리오 생성 / 치유) 가 호스트 Ollama 를 호출한다. 기본 모델은 OS 별로 자동 분기 — **Mac → `gemma4:26b`**, **WSL2 / Linux / Windows → `qwen3.5:9b`** (둘 다 호스트에 사전 pull 필요). 임베딩은 `bge-m3:latest`. Recording UI 의 Play (codegen) 만 쓸 거면 Ollama 미기동도 가능.

**Q. 컨테이너 안 코드를 바꾸려면?**
호스트 코드 수정 후 `./build.sh --redeploy` 로 image 재빌드 + 컨테이너 swap. 데이터 볼륨은 보존됨. `--reprovision` 옵션을 추가하면 provision 재실행 (KB·Jenkins 이력 등 데이터는 유지하되 Jenkins job 정의 / Dify chatflow 같은 baked-in 정의는 새 image 기준으로 재생성).

**Q. Recording UI 만 재기동하려면?**

```bash
./run-recording-ui.sh restart
```

호스트 venv 의 코드만 바꾼 경우 (executor / scenario validator 등) 컨테이너 재빌드 없이 이거면 충분.

**Q. Replay UI 만 재기동하려면?**

```bash
./run-replay-ui.sh restart
```

Recording UI launcher 와 동등 패턴 — env 자동 셋업 (PYTHONPATH / PLAYWRIGHT_BROWSERS_PATH / AUTH_PROFILES_DIR / MONITOR_HOME) + nohup detach + PID 관리. macOS / WSL2 / Linux / Windows(Git Bash) 모두 동작. `start` / `stop` / `restart` / `status` / `logs` / `foreground` / `doctor` 서브커맨드 지원.
