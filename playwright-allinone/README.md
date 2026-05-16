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

## 폴더 구조 (한눈에)

```text
playwright-allinone/
├── recording-ui/                ← Recording UI 본체 (호스트 데몬, 18092)
│   ├── recording_service/
│   └── run-recording-ui.sh
├── replay-ui/                   ← Replay UI 본체 (호스트 데몬 18093 / 휴대용 zip 18099)
│   ├── replay_service/
│   └── monitor/                 ← 명령줄 도구 (python -m monitor)
├── shared/                      ← 두 UI 가 함께 쓰는 공용 코드
│   ├── recording_shared/        ← trace 분석 · 실행 래퍼 · 보고서
│   └── zero_touch_qa/           ← 시나리오 엔진 · 자가 치유 · locator
├── replay-ui-portable-build/    ← Replay UI 휴대용 zip 빌드 (build-cache.sh + pack-*)
├── build.sh                     ← 녹화 PC Docker 이미지 빌드
├── mac-agent-setup.sh
├── wsl-agent-setup.sh
└── docs/
```

## 모니터링 PC 로 시나리오 옮기기

녹화 PC 에서 만든 `.py` 시나리오를 다른 PC 들에서 자동 실행하고 싶을 때 — **휴대용 모델 단일 흐름**.

### 휴대용 모델 한눈에

| 항목 | 값 |
|---|---|
| 받는 사람의 한 줄 액션 | `Launch-ReplayUI.bat` (Windows) 또는 `Launch-ReplayUI.command` (macOS arm64) 더블클릭 |
| 설치 / 관리자 권한 | 불필요 |
| 데이터 위치 | zip 폴더 안 `data/` (폴더 이동 시 데이터 같이 따라감) |
| USB 한 개로 여러 PC | 가능 (zip 풀린 폴더 자체가 독립) |
| 빌드 산출 | `DSCORE-ReplayUI-portable-{win64\|macos-arm64}-<ts>.zip` (OS 별 분리) |

### 공통 사용 흐름

1. **녹화 PC** — Recording UI 의 결과 카드 → `Original Script` 또는 `셀프힐링 후` 카드의 `⬇ 다운로드`. 받는 `.py` 는 평문 자격증명 자동 sanitize (`auth_flow.sanitize_script`) 통과.
2. **모니터링 PC** — 휴대용 zip 풀고 `Launch-ReplayUI.{bat,command}` 더블클릭 → <http://127.0.0.1:18099> 자동 오픈 → (a) 로그인 프로파일 등록 (비로그인 시나리오면 생략) → (b) `시나리오 스크립트` 카드에 `.py` 업로드 → (c) 프로파일 + verify URL 선택 → `▶ 실행` → 스텝별 스크린샷 + HTML 리포트.

### 휴대용 빌드 절차

```bash
# macOS arm64 빌드 머신 — 한 줄로 zip 까지 (캐시 채우기 + 자산 채우기 + zip 산출).
bash replay-ui-portable-build/pack-macos.sh

# Windows 빌드 머신 (PowerShell) — 캐시 채우기 + zip 산출 두 단계.
bash replay-ui-portable-build/build-cache.sh --target win64
powershell -NoProfile -ExecutionPolicy Bypass `
  -File replay-ui-portable-build/pack-windows.ps1 -MakeZip
```

옵션:

- `pack-macos.sh --no-zip` — `replay-ui/` 폴더만 갱신, zip 미산출 (pre-push hook 등 자산 refresh 전용).
- `bash replay-ui-portable-build/build-cache.sh --target {win64|macos-arm64|all}` — 캐시만 명시적으로 미리 채울 때 (pack-macos.sh 가 어차피 내부 호출하므로 보통 불필요).

녹화 + 휴대용 한 번에:

```bash
bash ../export-airgap.sh                    # 호스트 OS 가능 범위 양쪽
bash ../export-airgap.sh --replay-only      # 휴대용만
bash ../export-airgap.sh --target win64     # win64 zip 만
```

산출: `replay-ui-portable-build/build-out/DSCORE-ReplayUI-portable-{win64|macos-arm64}-<ts>.zip`. 받는 사람은 zip 풀고 `Launch-ReplayUI.bat` / `Launch-ReplayUI.command` 더블클릭만.

**OS 분기 정책** — `pack-windows.ps1` 는 PowerShell 전용, `pack-macos.sh` 는 macOS arm64 전용. Mac 빌드 머신만 양쪽 산출 가능 (Windows zip 은 pwsh 7 설치 시). Windows 빌드 머신은 win64 zip 만. Linux 네이티브는 휴대용 빌드 불가.

**자동 갱신 hook (개발자 PC, 권장)** — 본 저장소 안의 소스가 바뀐 채로 git push 하려고 하면, push 직전에 휴대용 자산이 자동으로 다시 채워진다. 한 번만 설정:

```bash
git config core.hooksPath .githooks
```

자세한 동작은 [`.githooks/README.md`](../.githooks/README.md).

### Replay UI 진입 요약

| 도구 | 위치 |
| --- | --- |
| Replay UI | <http://127.0.0.1:18099> (모니터링 PC 자기 자신만 접속, LAN 노출 X) |
| 휴대용 실행 | zip 폴더 안 `Launch-ReplayUI.bat` 또는 `Launch-ReplayUI.command` |
| CLI 실행 | `<embedded-python> -m monitor replay-script <script.py> --out <결과 폴더> [--profile <alias>] [--verify-url <URL>]` |
| CLI 로그인 등록 | `<embedded-python> -m monitor profile seed <프로파일이름> --target <사이트 URL>` |

> 2026-05-11 D17 일원화 — 이전 `bundle.zip` 흐름은 폐기됐다. 한 시나리오 = 한 `.py`. 적용할 로그인 프로파일과 verify URL 은 받는 쪽 Replay UI 에서 사용자가 명시 (또는 *비로그인* 으로 비워둠). 자세한 결정은 [PLAN_AUTH_PROFILE_NAVER_OAUTH.md §2 D17](docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md#2-의사결정-로그).

처음 보는 사용자가 따라할 수 있게 단계별로 정리한 문서는 [docs/replay-ui-guide.md](docs/replay-ui-guide.md). 통합 테스트 매트릭스는 [docs/replay-ui-integration-tests.md](docs/replay-ui-integration-tests.md).

## 가장 짧은 시작

```bash
cd playwright-allinone
chmod +x *.sh
./build.sh
```

이 명령은 image 빌드 + 컨테이너 기동 + 호스트 agent 연결까지 한 번에 한다 (첫 빌드 30~90분, 이후 3~10분). 완료 후 브라우저로 다음에 접속:

| 서비스 | URL | 기본 계정 |
| --- | --- | --- |
| Jenkins | <http://localhost:18080> | admin / password |
| Dify | <http://localhost:18081> | `admin@example.com` / `Admin1234!` |
| Recording UI | <http://localhost:18092> | (로컬 서비스, 계정 없음) |

자세한 설치 절차 (사전 요구사항, agent 자동/수동 연결, 첫 Pipeline 실행, 첫 녹화·재생) 는 [QUICKSTART](docs/quickstart.md) 에 단계별로 있다.

## 테스트 슈트 (e2e-test/)

2026-05-16 전면 재작성. 기존 daemon e2e 슈트 10개 + headed walkthrough 4개는 폐기. 설계 근거 + 폐기 진단은 [docs/PLAN_E2E_REWRITE.md](docs/PLAN_E2E_REWRITE.md), 자동화 불가 잔여는 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

CI 0대 전제 (github actions 비용 0 정책). 슬롯 4개 — 모두 로컬:

| 슬롯 | 발사 | 시간 예산 | 가드 |
| --- | --- | --- | --- |
| `pre-commit` | 매 commit | < 30s | `e2e-test/unit/` — emit/generator 결정론 단위 (외부 의존 0) |
| `pre-push` | 매 push | < 5min | `e2e-test/` 전체 + Replay UI 휴대용 자산 stale 자동 갱신 |
| build-time selftest | `./build.sh` 끝 | 환경별 | `e2e-test/selftest_build/run.sh` — D 그룹 (warn-only, 빌드 비차단) |
| receiving-PC selftest | `Launch-ReplayUI.{bat,command}` 최초 1회 | < 1min | `e2e-test/selftest_receive/run.py` — E 그룹 (Python/Playwright/Chromium/replay_service 자가진단) |

### 슈트 구조

```text
playwright-allinone/e2e-test/
├── unit/             # pre-commit 슬롯. 1 파일 = 1 회귀 commit (docstring 첫 줄에 hash 명시).
├── integration/      # pre-push 슬롯. Playwright + fixtures/ self-served HTML.
├── flow/             # pre-push 슬롯. Recording UI / Replay UI daemon round-trip.
├── fixtures/         # integration/ 과 flow/ 가 공유하는 self-served HTML (1 fixture = 1 회귀 패턴).
├── selftest_build/   # ./build.sh 끝에 호출.
└── selftest_receive/ # 휴대용 zip 안 첫 실행 시 호출.
```

### 설치 (개발자 PC 1회)

```bash
bash playwright-allinone/scripts/install-git-hooks.sh
```

이후 매 commit/push 마다 자동 발사. 우회: `git commit --no-verify` / `git push --no-verify` (긴급 핫픽스 외 금지).

### 회귀 가드 추가하는 법

새 회귀를 막는 슈트를 추가할 때는 다음 순서:

1. 회귀 commit hash 확정 + 어느 슬롯에 속하는지 결정 (unit / integration / flow).
2. `e2e-test/<slot>/test_<area>_<keyword>.py` 신설. docstring 첫 줄에 `Regression guard for <hash> — <한 줄 설명>`.
3. fixture HTML 필요하면 `e2e-test/fixtures/<keyword>.html` 신설. 1 fixture = 1 패턴.
4. 슬롯 시간 예산 (pre-commit < 30s, pre-push < 5min) 초과하지 않게 fixture 단순화.
5. 통과 확인 후 commit.

## build.sh 자세히

이 스크립트 한 개가 image 빌드 → 컨테이너 기동 → 데이터 초기화 → host agent 연결까지 모두 처리한다. 옵션 조합으로 "지금 무엇까지 할지" 를 선택한다.

### 어디서 실행하나

어디서 실행하든 `./build.sh` 한 번이면 빌드 → 컨테이너 → provision → 호스트 agent → Recording UI(18092) → 호스트 Replay UI(18093) 까지 자동 완료. 휴대용 Replay UI(18099) 는 받는 PC 가 별도 zip 풀고 띄움.

> **WSL2 호스트 분기 주의** — build.sh 가 WSL2 Ubuntu 안에서 실행되더라도 `wsl-agent-setup.sh` 는 Windows 호스트 네이티브 (Git Bash) 에 위임돼 돈다. Playwright 브라우저는 항상 호스트 네이티브에서 헤드드 창으로 떠야 하므로 (사용자 원칙) WSL2 안 직접 실행은 스크립트가 거부한다. Replay UI 도 같은 분기에서 `cmd.exe /c start` 를 거쳐 호스트의 `Launch-ReplayUI.bat` 으로 자동 시작.

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

기본 (인자 없이 `./build.sh`) — 빌드 + 기존 컨테이너 swap + provision 재실행 + **호스트 agent · Recording UI(18092) · 호스트 Replay UI(18093) 동시 자동 구동**. 데이터 볼륨(`dscore-data`) 은 보존하되 chatflow / Jenkins job / Dify provider 등록은 새 이미지 기준으로 재생성. 같은 머신에서 코드 수정 후 한 줄로 서비스 정상화까지 가는 의도.

| 옵션 | 역할 | 자주 쓰는 상황 |
|---|---|---|
| (없음) | 위 기본 동작 | 운영 표준 / dev iteration |
| `--no-redeploy` | 빌드만 (컨테이너 그대로). `--reprovision` 도 같이 OFF. | CI / 폐쇄망 export — `--tarball` 와 함께 |
| `--keep-provisioned` | 재기동 하되 provision 스킵 (`.app_provisioned` 유지) | 이미지 baked-in 정의 변경 없을 때 빠른 재기동 |
| `--no-agent` | 컨테이너만 기동, agent 연결 스킵 | CI 머신에서 컨테이너만 갱신 |
| `--fresh` | `dscore-data` 볼륨까지 삭제 (모든 데이터 폐기) | 처음부터 다시. 디버깅 막판. |
| `--no-cache` | buildx 레이어 캐시 무시하고 전체 레이어 재빌드 (30-90분) | Dockerfile / 베이스 이미지 / 시스템 라이브러리 의심 시 |
| `--tarball` | airgap 반출용 `dscore.ttc.playwright-<ts>.tar.gz` 산출 (`export-airgap.sh` 가 자동) | 다른 머신으로 옮길 배포 패키지 만들 때 |
| `-h`, `--help` | 도움말 출력 | |

backward-compat: 구 `--redeploy` · `--reprovision` 은 이제 default 이므로 명시해도 no-op. `--skip-tarball` 도 (deprecated) 기본이 미추출.

### 환경변수

| 변수 | 기본값 | 의미 |
|---|---|---|
| `OLLAMA_MODEL` | OS 별 자동 (Mac=`gemma4:26b`, WSL2/Linux/Windows=`qwen3.5:9b`) | Dify provider 에 등록될 LLM. chatflow YAML 의 placeholder 도 import 시 이 값으로 자동 치환. |
| `EMBEDDING_MODEL` | `bge-m3:latest` | Test Planning RAG KB 용 임베딩 모델. |
| `IMAGE_TAG` | `dscore.ttc.playwright:latest` | Docker image 태그 |
| `TARGET_PLATFORM` | `uname -m` 자동 감지 | `linux/arm64` (Apple Silicon) 또는 `linux/amd64`. 다른 아키 서버로 배포할 때만 override. |
| `OUTPUT_TAR` | `dscore.ttc.playwright-<ts>.tar.gz` | `--tarball` 시 산출 파일명 |
| `FORCE_PLUGIN_DOWNLOAD` | `false` | `true` 면 `jenkins-plugins/` `dify-plugins/` 에 파일이 있어도 재다운로드. 플러그인 버전 갱신 시만. |
| `AGENT_NAME` | OS 별 자동 (Mac=`mac-ui-tester`, 그 외=`wsl-ui-tester`) | Jenkins Node 이름 |
| `RECORDING_HOST_ROOT` | `~/.dscore.ttc.playwright-agent/recordings` | host 의 녹화 디렉토리 (컨테이너 `/recordings` 로 bind) |

### 시나리오별 명령

```bash
# 처음 한 번 / 코드 수정 후 / chatflow 변경 후 — 한 줄로 빌드 + 재기동 + reprovision + agent
./build.sh

# 이미지 baked-in 정의 안 바뀐 경우 — provision 스킵해서 빠르게
./build.sh --keep-provisioned

# 처음부터 다시 (모든 데이터 폐기)
./build.sh --fresh

# 베이스 이미지 / 시스템 라이브러리 의심 시 — 캐시 무시하고 전 레이어 재빌드 (30-90분)
./build.sh --no-cache

# WSL2 호스트에서 qwen3.5:9b 대신 다른 모델로 강제
OLLAMA_MODEL=qwen3-coder:30b ./build.sh

# CI 환경 — agent 연결 없이 컨테이너만 갱신
./build.sh --no-agent

# CI / 폐쇄망 export — 빌드만, 컨테이너 그대로
./build.sh --no-redeploy
./build.sh --tarball --no-redeploy             # tar.gz 까지 산출

# 플러그인 버전 올림 — hpi/difypkg 강제 재다운로드
FORCE_PLUGIN_DOWNLOAD=true ./build.sh
```

### 산출물

| 위치 | 무엇 |
|---|---|
| `./dscore.ttc.playwright-<timestamp>.tar.gz` | image 압축본 (`--tarball` 명시 시에만 생성, airgap 배포용, ~5-7GB) |
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
| [docs/replay-ui-guide.md](docs/replay-ui-guide.md) | **모니터링 PC 운영자 / 테스터 용 — Replay UI 설치 + 사용 가이드** (휴대용 단일 모델) |
| [docs/replay-ui-integration-tests.md](docs/replay-ui-integration-tests.md) | Replay UI · 모니터링 PC bundle 흐름 통합 테스트 매트릭스 (자동 22 + 수동 20) |
| [docs/reference.md](docs/reference.md) | 포트 / 볼륨 / 환경변수 / 데이터 구조 / DSL 액션 14종 / API 계약 |
| [docs/recording-troubleshooting.md](docs/recording-troubleshooting.md) | 자주 발생하는 녹화·재생 에러 모음 |
| [docs/](docs/) | 결정 문서 (PLAN_*.md) — 설계 배경 / 트레이드오프 / 검증 |
| [docs/PLAN_DSL_COVERAGE.md](docs/PLAN_DSL_COVERAGE.md) | DSL 표현력 정량 분석 — 임의 웹사이트의 ~70~75% 동작 커버, 미커버 영역 명시 |
| [docs/PLAN_EXTERNAL_TRUST.md](docs/PLAN_EXTERNAL_TRUST.md) | 외부 신뢰성 강화 패키지 — 호환성 진단(트랙 3) + 측정 액션 확장(트랙 2 Phase B1) 의사결정 |
| [`.githooks/README.md`](../.githooks/README.md) | 휴대용 자산 자동 갱신 hook 설정 (개발자, opt-in) |

## Recording UI 의 핵심 능력 (요약)

자세한 건 [RECORDING_UI 문서](docs/recording-ui.md) 에. 요약하면:

- **🔐 Login Profile** — 테스트 대상의 로그인 세션을 한 번 시드 → 매 녹화·재생마다 재사용.
- **🔍 Discover URLs** — 사이트 URL 자동 수집 → tour 시나리오 자동 생성.
- **🎬 Recording** — Playwright codegen 으로 사용자 조작 녹화 → 14-DSL 변환.
- **▶️ Play & more** — 두 가지 재생 모드 (codegen 자가 치유 / Dify LLM 치유).
- **자동 정리** — 중복 click 압축, IME 노이즈 키 제거 (CapsLock/Unidentified), popup race fallback, transient alert skip 등 codegen 부산물을 자동으로 정리해 시나리오가 깨지지 않게 한다.

## SUT 호환성 사전 진단 (`compat-diag`)

테스트 대상 사이트가 본 솔루션으로 자동화 가능한지 *시도 전*에 알 수 있다.
도메인 URL 한 줄 입력 → DOM 스캔 → 60초 안에 5종 카테고리 판정 리포트.

```bash
# CLI (모니터링 PC)
python -m monitor compat-diag https://target.example.com --output report.html

# Replay UI 카드 → "호환성 진단" → URL 입력 → JSON + HTML 결과
# 또는 직접 호출
POST /api/compat-diag  {"url": "https://target.example.com"}
```

### 판정 카테고리

| Verdict | 의미 | CLI exit |
| --- | --- | --- |
| `compatible` | DSL 커버 영역 안 | 0 |
| `limited` | 일부 동작 별 트랙 필요 (WebSocket / Dialog / canvas-heavy) | 0 |
| `incompatible:closed-shadow` | closed Shadow DOM (브라우저 정책상 자동화 불가) | 2 |
| `incompatible:captcha` | reCAPTCHA / hCaptcha / Turnstile 감지 | 2 |
| `unknown` | 페이지 로드 실패 또는 timeout | 2 |

감지 신호: closed/open shadow root 카운트, iframe 도메인 목록, WebSocket /
EventSource 호출, 페이지 로드 중 dialog 호출, canvas/SVG 면적 비율, 프레임워크
핑거프린트(React / Vue / Lit / Angular). 자세한 동작은
[shared/zero_touch_qa/compat_diag.py](shared/zero_touch_qa/compat_diag.py).

설계 의사결정은 [docs/PLAN_EXTERNAL_TRUST.md §3](docs/PLAN_EXTERNAL_TRUST.md) 참조.

## 외부 SUT 벤치마크 (트랙 2 Phase B2)

DSL 표현력의 임의 사이트 일반화 데이터를 확보하는 트랙. 공개 안정 사이트 9개에
시나리오 50개를 작성하고 N회 반복 실행해 flake rate 측정.

```bash
cd playwright-allinone
PYTHONPATH=shared:recording-ui:replay-ui:test \
  python -m bench.flake_runner --runs 10

PYTHONPATH=shared:recording-ui:replay-ui:test \
  python -m bench.dashboard
# 결과: test/bench/dashboards/index.html
```

baseline 1회 (2026-05-13): 50 시나리오 중 PASS 30 / FAIL 20 (첫 그린 60%).

- **상위 안정**: TodoMVC 100% / herokuapp 80% / saucedemo 75% / practicesoftwaretesting 80%
- **하위**: demoqa / wikipedia / hackernews / playwright_dev — 외부 사이트 selector 변동성
- **Salesforce Trailhead**: closed shadow 의도적 FAIL (호환성 진단 도구 검증 데이터)

정기 실행 (daily cron 시계열 누적) 은 **추후 별도 서비스 내부 구현**으로 대체.
본 레포는 *실행 인프라 + 시나리오 50개* 만 자산화. 자세한 설계는
[test/bench/README.md](test/bench/README.md), 의사결정은
[docs/PLAN_EXTERNAL_TRUST.md §5](docs/PLAN_EXTERNAL_TRUST.md).

DSL 표현력 범위는 [docs/PLAN_DSL_COVERAGE.md](docs/PLAN_DSL_COVERAGE.md) 에 정량
기록 — Sprint 6 측정 액션 추가로 임의 웹사이트의 ~70~75% 동작 표현 가능.

## 자주 묻는 것

**Q. Jenkins 와 Recording UI 중 어느 쪽을 써야 하나?**
요구사항이 글로 잘 정의되어 있으면 Jenkins (LLM 이 시나리오 작성). 화면을 직접 보면서 클릭으로 정의하는 게 빠르면 Recording UI. 둘은 같은 시나리오 포맷을 공유.

**Q. 호스트 Ollama 가 꼭 필요한가?**
Jenkins Pipeline 의 LLM 단계 (시나리오 생성 / 치유) 가 호스트 Ollama 를 호출한다. 기본 모델은 OS 별로 자동 분기 — **Mac → `gemma4:26b`**, **WSL2 / Linux / Windows → `qwen3.5:9b`** (둘 다 호스트에 사전 pull 필요). 임베딩은 `bge-m3:latest`. Recording UI 의 Play (codegen) 만 쓸 거면 Ollama 미기동도 가능.

**Q. 컨테이너 안 코드를 바꾸려면?**
호스트 코드 수정 후 `./build.sh` 한 줄. image 재빌드 + 컨테이너 swap + provision 재실행이 기본이라 chatflow / Jenkins job / Dify provider 등 baked-in 정의도 새 image 기준으로 재생성된다. 데이터 볼륨은 보존. provision 단계가 무거우면 `--keep-provisioned` 로 skip 가능 (이미지 baked-in 정의 변경이 없을 때만 의미).

**Q. 빌드 시 어떤 UI 가 동시에 뜨나?**

`./build.sh` 한 번이 다음 4 가지를 동시 자동 구동한다.

| 서비스 | 포트 | 어디서 도는가 | 띄우는 주체 |
|---|---|---|---|
| Jenkins / Dify | 18080 / 18081 | 컨테이너 | build.sh step 5-2 (`docker run`) |
| Recording UI | 18092 | 호스트 (Mac venv / Windows venv) | agent-setup step 6.5 |
| 호스트 Replay UI | 18093 | 호스트 (venv uvicorn) | agent-setup step 6.6 |
| 휴대용 Replay UI | 18099 | 받는 PC (휴대용 embedded-python) | 받는 PC 가 `Launch-ReplayUI.{bat,command}` 더블클릭 (build.sh 는 띄우지 않음) |
| Jenkins agent | — | 호스트 (Mac / Windows 네이티브) | build.sh step 5-4 (`*-agent-setup.sh`) |

위 4 종(컨테이너 + Recording UI + 호스트 Replay UI) 은 모두 별도 명령을 칠 필요 없다. 빌드 끝나면 브라우저로 18080 / 18081 / 18092 / 18093 네 곳을 그대로 열면 된다. 휴대용 Replay UI(18099) 는 받는 PC 흐름이라 빌드와 무관.

**Q. Recording UI 만 재기동하려면?**

```bash
./recording-ui/run-recording-ui.sh restart
```

호스트 venv 의 코드만 바꾼 경우 (executor / scenario validator 등) 컨테이너 재빌드 없이 이거면 충분.

**Q. Replay UI 만 재기동하려면?**

휴대용 폴더 안에서:

```cmd
Stop-ReplayUI.bat   :: Windows
Launch-ReplayUI.bat
```

```bash
./Stop-ReplayUI.command   # macOS arm64
./Launch-ReplayUI.command
```

env (PYTHONPATH / PLAYWRIGHT_BROWSERS_PATH / AUTH_PROFILES_DIR / MONITOR_HOME) 와 PID 관리는 launcher 가 자동 처리.
