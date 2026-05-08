# Replay UI — 모니터링 PC 설치 & 사용 가이드

녹화 PC 에서 만든 시나리오 zip 을 받아 다른 PC 에서 무인 실행하고, 결과를 한 화면에서 검증하는 작은 웹 앱.

> **이 문서는 모니터링 PC 운영자 / 테스터 용**. 녹화 PC 쪽 안내는 [RECORDING_UI](../playwright-allinone_RECORDING_UI.md), 큰 그림은 [README](../README.md).

---

## 1. 한 줄 요약

| 항목 | 값 |
|---|---|
| 주소 | <http://127.0.0.1:18094> (모니터링 PC 자기 자신만 접근) |
| 포트 | 18094 (Recording UI 18092 와 별개) |
| 어디 도는가 | 사용자 startup task — Mac=launchd / Windows=Task Scheduler / Linux=systemd --user |
| Docker | 안 씀. 호스트 네이티브 venv + Chromium |
| LAN 노출 | 없음 (127.0.0.1 only) |

---

## 2. 설치 — 자동 (1회)

### 2.1 사전 준비

- Python 3.11 설치 (Mac/Linux 는 `python3`, Windows 는 [python.org](https://www.python.org/downloads/) 또는 `winget install Python.Python.3.11`)
- 디스크 여유 1GB 이상 (Chromium + venv)

### 2.2 monitor-runtime 패키지 받기

| 경로 | 누가 |
|---|---|
| GitHub Release `monitor-runtime-vX.Y.Z` | 가장 흔한 경로. `monitor-runtime-<ts>.zip` 첨부 |
| 사내 빌드 | `bash playwright-allinone/monitor-build/build-monitor-runtime.sh` 직접 실행 |

받은 zip 을 임시 폴더에 풀기.

### 2.3 한 줄 설치

**Mac · Linux**
```bash
cd <unzipped>
bash install-monitor.sh --register-startup --register-task
```

**Windows (PowerShell)**
```powershell
cd <unzipped>
powershell -ExecutionPolicy Bypass -File install-monitor.ps1 -RegisterStartup -RegisterTask
```

옵션 의미:

| 옵션 | 효과 |
|---|---|
| `--register-startup` / `-RegisterStartup` | Replay UI 를 사용자 로그인 시 자동 기동 |
| `--register-task` / `-RegisterTask` | 30분 주기 스케줄러 등록 안내 출력 (bundle 별로 사용자가 마무리) |

설치 스크립트가 만드는 것:

```
~/.dscore.ttc.monitor/
├── venv/                # Python 가상환경
├── chromium/            # Playwright 브라우저
├── auth-profiles/       # alias 별 storage_state 카탈로그 (자동 생성)
├── scenarios/           # bundle.zip 두는 곳
└── runs/                # 실행 결과 누적
```

### 2.4 설치 완료 확인

설치 직후 브라우저로 <http://127.0.0.1:18094> 접속. 헤더 `🎬 Replay UI` 가 보이면 OK.

만약 안 떠 있으면 수동 기동:

```bash
# Mac/Linux
PLAYWRIGHT_BROWSERS_PATH=~/.dscore.ttc.monitor/chromium \
AUTH_PROFILES_DIR=~/.dscore.ttc.monitor/auth-profiles \
MONITOR_HOME=~/.dscore.ttc.monitor \
~/.dscore.ttc.monitor/venv/bin/python -m uvicorn replay_service.server:app \
    --host 127.0.0.1 --port 18094
```

```powershell
# Windows
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:USERPROFILE\.dscore.ttc.monitor\chromium"
$env:AUTH_PROFILES_DIR = "$env:USERPROFILE\.dscore.ttc.monitor\auth-profiles"
$env:MONITOR_HOME = "$env:USERPROFILE\.dscore.ttc.monitor"
& "$env:USERPROFILE\.dscore.ttc.monitor\venv\Scripts\python.exe" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094
```

---

## 3. 첫 사용 (alias 시드 → bundle 업로드 → 실행)

UI 헤더의 `🧭 첫 사용 가이드` 버튼이 같은 4단계를 정리해서 보여줍니다.

### 3.1 alias 시드 — Login Profile 카드

1. `+ alias 추가` 클릭
2. 모달에 두 칸 입력:
   - **alias 이름** — 보통 `packaged` (녹화 PC 의 bundle 모달에 적은 이름과 같아야 함)
   - **target URL** — 사이트 진입 URL (보통 메인 페이지)
3. `✓ 시드 시작` → 자동으로 브라우저 창 열림
4. 브라우저에서 **사람이 직접 로그인** (CAPTCHA / MFA 등 무엇이든 통과)
5. 로그인 완료된 페이지에서 **창 닫기** → storage 자동 저장
6. Login Profile 카드 행에 `Storage = 시드됨` 으로 갱신

### 3.2 bundle 업로드 — Bundle 카드

1. `⬆ 업로드` 클릭 → 녹화 PC 에서 받은 `<sid>.bundle.zip` 선택
2. 같은 이름이 이미 있으면 덮어쓰기 confirm prompt → 확인
3. 행에 `시나리오 / alias / 등록일 / 크기 / [▶ 실행]` 노출

### 3.3 첫 실행

`▶ 실행` 클릭 → Run 카드 활성화. SSE 로 jsonl 이벤트 라이브 스트림.

> alias 가 시드 안 됐으면 `▶ 실행` 버튼이 **자동으로 비활성** + tooltip "alias 시드 필요". 시드부터 하세요.

### 3.4 결과 검증 — Results 카드 → [상세 →]

- 좌측 스텝 리스트, 우측 스크린샷
- 스크린샷 클릭 → lightbox 확대
- 실패 스텝은 빨간 강조 + 실패 직전 화면
- `📥 HTML 리포트` → 단일 HTML 파일 다운로드 (외부 PC 에 첨부 가능)

---

## 4. 일상 운영

### 4.1 자동 운영 (사람 개입 X)

`--register-task` 등록 후 30분마다 스케줄러 트리거. 정상 동안은 사람이 화면을 안 봐도 됨. 결과는 `~/.dscore.ttc.monitor/runs/<run-id>/` 누적.

### 4.2 만료 알람 대응

만료된 alias 가 있으면:

- 헤더에 `🔴 N 시드 만료` 글로벌 배지
- Login Profile 카드 alias 행에 `🔴 시드 필요`
- Results 행에 `⚠ 시드 만료`

대응:

1. Login Profile 카드 → 만료된 alias 행의 `↻ Re-seed` 클릭
2. URL 입력 → 브라우저 자동 열림 → 직접 로그인 → 창 닫기
3. 다음 스케줄러 트리거에 자동 통과

### 4.3 시나리오 갱신

녹화 PC 가 갱신된 bundle.zip 을 배포하면:

1. Bundle 카드의 기존 행 `🗑` 또는 같은 파일명으로 다시 업로드
2. 동일 이름 덮어쓰기 confirm 후 교체

---

## 5. CLI (헤드리스 / 자동화)

GUI 없이 같은 일을 명령어로:

| 명령 | 동작 |
|---|---|
| `python -m monitor profile list` | 카탈로그 alias 상태 표 |
| `python -m monitor profile seed <alias> --target <url>` | 수동 시드 (브라우저 열림) |
| `python -m monitor profile delete <alias>` | alias 제거 |
| `python -m monitor replay <bundle.zip> --out <dir>` | bundle 1회 실행 (probe → script → trace) |

`monitor` 모듈은 `~/.dscore.ttc.monitor/venv/bin/python` (Mac/Linux) 또는 `~\.dscore.ttc.monitor\venv\Scripts\python.exe` (Windows) 로 실행해야 site-packages 에 있는 모듈을 찾습니다.

### exit code

| code | 의미 | 운영자 액션 |
|---|---|---|
| 0 | 성공 | — |
| 1 | 시나리오 step 실패 (인증 외) | Replay UI [상세→] 로 결과 검증 |
| 2 | 시스템 오류 | 엔지니어 조사 |
| 3 | storage 만료/미시드 | Re-seed |

---

## 6. 폴더 구조 / 데이터 경로

```
~/.dscore.ttc.monitor/
├── venv/                                    # Python 가상환경
├── chromium/                                # Playwright Chromium
├── auth-profiles/                           # storage_state 카탈로그
│   ├── _index.json
│   ├── _index.lock
│   └── <alias>.storage.json
├── scenarios/                               # 업로드된 bundle 들
│   └── <name>.bundle.zip
├── runs/                                    # 실행 결과
│   └── <run-id>/
│       ├── run_log.jsonl                    # 이벤트 로그
│       ├── codegen_run_log.jsonl            # 스텝 단위 결과
│       ├── trace.zip                        # Playwright trace
│       ├── screenshots/                     # 스텝별 PNG
│       ├── exit_code                        # 단일 정수
│       └── meta.json                        # 메타 (alias / provenance / 시각)
├── replay-ui.stdout.log                     # Replay UI 로그
└── replay-ui.stderr.log
```

---

## 7. 보안 정책

| 항목 | 정책 |
|---|---|
| Replay UI bind | 127.0.0.1 only (LAN 노출 X) |
| 자격증명 | 모니터링 PC 어디에도 평문 저장 X — 사람이 브라우저에 직접 입력 |
| storage_state | 모니터링 PC 의 카탈로그에만 보관, PC 간 공유 안 함 |
| bundle.zip | storage_state / 평문 PW / 호스트 절대경로 절대 미포함 |
| 실행 모드 | 사용자 startup task (사용자 권한). OS 서비스/데몬 X (브라우저 GUI 띄우기 위해) |

---

## 8. 자주 막히는 곳

| 증상 | 원인 / 해결 |
|---|---|
| Replay UI 가 안 뜸 | `~/.dscore.ttc.monitor/replay-ui.stderr.log` 확인. 보통 venv 의 Python 문제. 수동 기동 명령(§2.4) 시도 |
| `▶ 실행` 비활성 | bundle 의 alias 가 카탈로그에 미시드. Login Profile 카드 → 해당 alias 행 → `🌱 시드` |
| 매번 30분마다 만료 | probe URL 이 잘못 — 로그인 페이지로 잡히면 항상 expired. 녹화 PC 의 bundle 다운로드 모달에서 probe URL 을 "이미 로그인된 페이지" 로 다시 입력해 새 bundle 받기 |
| 시드 후에도 만료 | 사이트가 storage 를 짧게 유지 (수 시간 만료). 사이트의 보안 정책. 일정 주기로 사람이 Re-seed 하는 게 정상 동작 |
| LAN 의 다른 PC 에서 접속 안 됨 | 의도된 정책 (D10). 다른 PC 에서 결과 확인이 필요하면 모니터링 PC 에서 HTML 리포트 다운로드 후 공유 |
| Windows 에서 Re-seed 시 브라우저 안 뜸 | Replay UI 가 OS 서비스로 등록된 상태일 가능성. 사용자 startup task 로만 등록되도록 재설치 (`-RegisterStartup` 옵션) |

---

## 9. 추가 정보

| 문서 | 무엇 |
|---|---|
| [README](../README.md) | 큰 그림 (녹화 PC ↔ 모니터링 PC) |
| [RECORDING_UI](../playwright-allinone_RECORDING_UI.md) | 녹화 PC 쪽 — bundle 만들기 |
| [replay-ui-integration-tests.md](replay-ui-integration-tests.md) | 통합 테스트 매트릭스 |
| `.claude/plans/squishy-wishing-emerson.md` | 설계 결정 (D1–D11) + 동선 / 보안 정책 상세 |
