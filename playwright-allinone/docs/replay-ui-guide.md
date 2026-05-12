# Replay UI 설치·사용 가이드

이 문서는 **녹화 PC 에서 만든 시나리오를 다른 PC 에서 자동으로 돌리고 싶은 사람** 을 위한 가이드다.

처음부터 끝까지 따라가면 다음을 한 번 성공시킬 수 있다.

1. 모니터링 PC 에 Replay UI 설치
2. 처음 한 번 사이트에 로그인 (사람이 직접 — CAPTCHA / OTP 든 무엇이든)
3. 녹화 PC 에서 받은 zip 파일 업로드
4. 실행 결과를 화면에서 검증

> 이 시스템 전체 그림이 처음이면 [README](../README.md) 를 먼저 본다. 녹화 PC 쪽 사용법은 [recording-ui.md](recording-ui.md).

---

## 1. 먼저 이해할 것

### 1.1 이 도구가 푸는 문제

녹화 PC 에서 Playwright 로 시나리오를 만들면, 그 시나리오는 **로그인된 상태** 를 전제로 동작한다. 그런데 시나리오 파일만 다른 PC 로 복사하면 그 PC 에는 로그인 흔적이 없어서 실행이 실패한다.

Replay UI 는 이 갭을 메운다. **로그인은 모니터링 PC 에서 사람이 한 번 직접 한다**. 한 번 로그인해 두면, 같은 사이트의 시나리오 zip 을 몇 개 가져오든 모두 그 로그인 상태를 재사용해서 자동 실행한다.

### 1.2 자동 재로그인은 안 한다

이 도구는 **세션이 만료되면 사람이 다시 로그인하는 방식** 이다. 자동 재로그인은 일부러 빼 놨다 — CAPTCHA / 2단계 인증 / 디바이스 신뢰 챌린지가 있는 사이트에서는 자동화 자체가 부작용 (계정 잠금, 보안 알림, 감사 로그 오염) 을 일으키기 때문이다.

대신 만료되면 화면에 빨간 알람이 뜨고, **[다시 로그인]** 버튼 한 번 누르면 브라우저가 다시 열려서 사람이 로그인하면 된다.

### 1.3 두 가지 PC 역할

|  | 녹화 PC (1대) | 모니터링 PC (N대) |
|---|---|---|
| 무엇을 한다 | 시나리오 작성 → zip 으로 export | 받은 zip 을 자동으로 반복 실행 + 결과 검증 |
| 어떤 도구 | Recording UI (포트 18092) | **Replay UI (포트 18094)** ← 이 문서 |
| 빈도 | 시나리오 만들 때 | 30분마다 자동 (옵션) |

같은 PC 가 두 역할 겸임도 가능하다 (개발 환경).

---

## 2. 용어 5개

이 문서에서 반복해서 쓰는 단어들. 처음 한 번만 익혀 두면 이후 가이드를 그대로 따라할 수 있다.

| 단어 | 뜻 |
|---|---|
| **로그인 프로파일 (이름)** | 사이트 1개 + 계정 1개의 로그인 상태에 붙이는 이름. 예 — 사이트 `dpg.example.com` 에 계정 `qa01` 로 로그인한 상태를 `dpg-qa` 라는 이름으로 저장 |
| **로그인 등록** | 처음 한 번 그 프로파일 이름으로 사이트에 로그인하는 행위. 브라우저가 자동으로 열리고, 사람이 직접 ID/PW (또는 OTP, 소셜 로그인 등) 입력 → 창 닫기 → 로그인 상태가 모니터링 PC 에 저장된다 |
| **시나리오 스크립트** | 녹화 PC 가 만든 한 개의 `.py` 파일. Playwright 코드 본문이며, sanitize 통과로 평문 자격증명은 placeholder 처리되어 있다. 파일명은 보통 `original.py` 또는 `regression_test.py` (LLM 셀프힐링 후 강화본). *D17 (2026-05-11) 일원화 — 이전 `<세션ID>.bundle.zip` 흐름 폐기* |
| **로그인 상태 확인** | 매 실행 직전에 "내가 가진 로그인 상태가 아직 살아 있나?" 를 5초 안에 GET 1회로 빠르게 본다. 만료면 즉시 중단 + 알람 (실행 안 함) |
| **모니터링 PC 설치 패키지** | Replay UI 를 모니터링 PC 에 설치하기 위한 zip. 파일명 `monitor-runtime-<날짜시각>.zip`. Python 가상환경 / 브라우저 / 소스가 모두 들어 있어 폐쇄망에서도 한 번에 셋업된다 |

---

## 3. 준비물

| 항목 | 값 |
|---|---|
| OS | macOS 12+ / Ubuntu 22.04+ / Windows 10·11 |
| Python 3.11.x | Windows 는 zip 안의 Python 3.11 installer 로 자동 준비. Mac/Linux 는 `python3` 로 확인. monitor-runtime wheel 번들은 cp311 전용입니다 |
| 디스크 여유 | 1GB 이상 (브라우저 + 가상환경) |
| 권한 | 일반 사용자 권한이면 충분 (관리자 권한 / sudo 불필요) |
| 네트워크 | 설치 시 외부 인터넷 불필요 (패키지 zip 안에 모두 포함) |

설치된 Python 확인:

```bash
# Mac / Linux
python3 --version
```

```powershell
# Windows
python --version
```

Mac/Linux 는 `Python 3.11.x` 가 보이면 OK. `Python 3.12+`, `3.13+`, `3.14+` 는 이 오프라인 wheel 번들과 맞지 않습니다. Windows 는 설치 스크립트가 3.11 을 찾지 못하면 zip 안의 installer 로 `%USERPROFILE%\.dscore.ttc.monitor\python311` 아래에 자동 설치합니다.

---

## 4. 설치 — 한 번만

### 4.1 설치 패키지 받기

`monitor-runtime-<날짜시각>.zip` 을 받는다. 받는 곳은 두 군데 중 하나:

| 방법 | 어디서 |
|---|---|
| GitHub Release | 운영팀이 미리 빌드해 둔 zip 을 Release 페이지에서 다운로드 |
| 직접 빌드 | 빌드 머신에서 `bash playwright-allinone/monitor-build/build-monitor-runtime.sh` 실행 → 산출 zip 을 모니터링 PC 로 복사 |

받은 zip 을 임시 폴더에 푼다. 압축 푼 폴더 안에 `install-monitor.sh` (Mac/Linux) 또는 `install-monitor.cmd` / `install-monitor.ps1` (Windows) 가 있어야 한다.

### 4.2 Mac / Linux 설치

압축 푼 폴더에서:

```bash
bash install-monitor.sh --register-startup --register-task
```

옵션 의미:

| 옵션 | 효과 |
|---|---|
| `--register-startup` | 사용자 로그인 시 Replay UI 가 자동으로 떠 있도록 등록한다. 안 주면 매번 수동 기동해야 한다. |
| `--register-task` | 30분 주기로 시나리오를 자동 실행하는 스케줄러 등록 *안내* 를 출력한다. 시나리오가 등록된 뒤에 안내문대로 한 줄 더 실행하면 된다. |

처음 설치라면 둘 다 주는 것을 권장한다.

### 4.3 Windows 설치

PowerShell 을 일반 사용자로 (관리자 권한 X) 열고, 압축 푼 폴더로 이동 후:

```powershell
.\install-monitor.cmd
```

Windows 는 이 한 번으로 Python 3.11 준비, venv/패키지/Chromium/모듈 설치, 로그인 시 자동시작 등록, 현재 세션의 Replay UI 실행까지 처리한다. `install-monitor.cmd` 는 PowerShell 실행 정책에 막히지 않도록 `install-monitor.ps1` 을 `-ExecutionPolicy Bypass` 로 호출하는 얇은 래퍼다.
설치 후에는 `%USERPROFILE%\.dscore.ttc.monitor\open-replay-ui.cmd` 또는 바탕화면의 `DSCORE Replay UI` 바로가기를 더블클릭하면 된다. 이 실행 파일은 Replay UI 가 꺼져 있으면 백그라운드로 띄우고 브라우저까지 연다.
필요할 때만 `-NoRegisterStartup`, `-NoStart`, `-RegisterTask` 옵션을 추가한다.

### 4.4 설치가 만든 것

설치가 끝나면 사용자 홈 아래에 다음이 생긴다.

```
~/.dscore.ttc.monitor/             ← Mac/Linux
%USERPROFILE%\.dscore.ttc.monitor\ ← Windows

├── venv/                          Python 가상환경 (Replay UI 가 사용)
├── chromium/                      Playwright 가 띄울 브라우저
├── auth-profiles/                 로그인 상태 보관함 (등록할 때마다 여기에 쌓임)
├── scripts/                       업로드한 시나리오 .py 들 (D17)
└── runs/                          실행 결과 (스크린샷, 로그)
```

`--register-startup` 을 줬다면 OS 가 자동으로 Replay UI 를 띄운다. 어떤 방식으로 등록되는지:

| OS | 등록 방식 |
|---|---|
| Mac | `~/Library/LaunchAgents/dscore.replay-ui.plist` (사용자 LaunchAgent) |
| Linux | `~/.config/systemd/user/replay-ui.service` (systemd --user) |
| Windows | 작업 스케줄러 → `DSCORE Replay UI` (At log on, 일반 사용자 권한) |

### 4.5 설치 확인

웹브라우저에서 다음 주소로 접속:

```
http://127.0.0.1:18094
```

페이지 상단에 `🎬 Replay UI` 와 4개 카드 (`로그인 프로파일`, `시나리오 스크립트`, `실행`, `결과`) 가 보이면 설치 OK.

> 이 주소는 **모니터링 PC 자기 자신에서만** 접속된다. 다른 PC 에서 LAN 으로 접속해도 거부된다 (의도된 보안 설정).

### 4.6 안 떴을 때 — 수동 기동

브라우저로 접속했는데 페이지가 안 뜨면 OS 가 startup task 를 안 띄운 상태다. **단일 진입점 launcher** 한 줄로 끝 (Recording UI 의 `run-recording-ui.sh` 와 동등 패턴 — env 자동 셋업 + nohup detach + PID 관리. macOS / WSL2 / Linux / Windows(Git Bash) 모두 동작):

```bash
# 저장소 안에서:
./playwright-allinone/replay-ui/run-replay-ui.sh restart

# 다른 서브커맨드:
./playwright-allinone/replay-ui/run-replay-ui.sh start     # 백그라운드 기동
./playwright-allinone/replay-ui/run-replay-ui.sh stop      # 중지
./playwright-allinone/replay-ui/run-replay-ui.sh status    # health 점검 + PID
./playwright-allinone/replay-ui/run-replay-ui.sh logs      # 로그 follow
./playwright-allinone/replay-ui/run-replay-ui.sh foreground  # 현재 터미널에서 실행 (디버그)
./playwright-allinone/replay-ui/run-replay-ui.sh doctor    # python 모듈 확인
```

env 는 launcher 가 자동 export — `PLAYWRIGHT_BROWSERS_PATH` / `AUTH_PROFILES_DIR` / `MONITOR_HOME` / `PYTHONPATH` 를 사용자가 직접 박을 필요 없음. `MONITOR_HOME` env 만 override 하면 다른 install_root 도 가능.

`./replay-ui/run-replay-ui.sh status` 가 `health: ok` 와 PID 를 찍으면 OK. 다시 브라우저로 접속해 본다.

문제가 계속되면 [§9 자주 막히는 곳](#9-자주-막히는-곳) 으로.

---

## 5. 첫 실행 — 4단계

설치가 끝났으면 화면에는 카드 4개가 비어 있는 상태다. 다음 4단계를 순서대로 하면 첫 실행이 끝난다.

> 화면 상단의 `🧭 첫 사용 가이드` 버튼이 같은 4단계를 위저드로 안내한다. 처음에는 위저드를 따라가도 좋다.

### 5.1 로그인 프로파일 등록

**왜 하는가** — 시나리오를 실행하려면 사이트에 로그인된 상태가 필요하다. 그 로그인을 모니터링 PC 에서 한 번 사람이 직접 해서 저장해 둔다.

**절차**

1. `🔐 로그인 프로파일` 카드의 `[+ 새 프로파일]` 클릭
2. 모달에 두 가지 입력:
   - **프로파일 이름** — 직접 정한다. 예 — `dpg-qa`. 녹화 PC 에서 zip 을 만들 때 적은 이름과 **반드시 같아야** 한다.
   - **사이트 주소** — 그 사이트의 메인 페이지 URL. 예 — `https://dpg.example.com/`
3. `✓ 로그인 시작` 누르면 새 브라우저 창이 열린다.
4. 그 창에서 **사람이 직접 로그인** 한다. ID/PW 든, OTP 든, 소셜 로그인이든 무엇이든 OK.
5. 로그인이 끝나서 메인 페이지가 보이는 상태에서 **그 창을 그냥 닫는다**.
6. 카드의 해당 행에 `로그인 상태 = 등록됨` 이 표시되면 성공.

**잘 안 되는 경우 — 다음 두 가지가 가장 흔하다.**

- 브라우저가 안 열린다 → OS startup task 가 OS 서비스로 잘못 등록된 경우다. `--register-startup` 옵션으로 다시 설치한다 (사용자 권한으로 떠야 GUI 창을 열 수 있다).
- 로그인은 했는데 등록 안 됐다 표시 → 사이트가 `localStorage` 만 쓰고 쿠키를 안 남기는 케이스. 드물지만 있다. 이 경우 시나리오 작성자에게 문의.

### 5.2 시나리오 스크립트 받아오기 (녹화 PC 에서)

**왜 하는가** — 녹화 PC 가 만든 한 개의 `.py` 가 모니터링 PC 에서 1개 시나리오로 실행된다 (D17 일원화 — 이전 `bundle.zip` 흐름은 폐기).

**녹화 PC 작업** ([recording-ui.md §9](recording-ui.md))

1. 녹화 PC 의 Recording UI 에서 시나리오 녹화를 끝낸다.
2. 결과 카드 → `Original Script` 카드 (또는 셀프힐링 후 생성된 `regression_test.py` 카드) → `⬇ 다운로드` 클릭.
3. 응답은 **`auth_flow.sanitize_script` 통과한 `.py`** — 평문 자격증명은 placeholder 처리됨.

이 `.py` 를 USB / 이메일 / 사내 공유 폴더 등으로 모니터링 PC 에 옮긴다.

### 5.3 시나리오 스크립트 업로드

**왜 하는가** — Replay UI 가 `.py` 를 보관해 두고 사용자가 ▶ 실행 누를 때 호출.

**절차**

1. `📄 시나리오 스크립트` 카드의 `⬆ 업로드 (.py)` 클릭
2. 받은 `.py` 파일 선택
3. 같은 이름이 이미 있으면 "덮어쓸까요?" 묻는다. 갱신이면 확인.
4. 적용할 로그인 프로파일 select — §5.1 에서 등록한 프로파일 이름 (예 `dpg-qa`) 또는 *비로그인 — storage_state 미주입* 선택.
5. (선택) verify URL 입력 — 비우면 프로파일 카탈로그의 `verify.service_url` 사용. 비로그인 시나리오면 verify 자체가 skip.
6. 카드에 한 줄 추가됨:

   | 스크립트 | 등록일 | 크기 | 액션 |
   |---|---|---|---|
   | dashboard-tour.py | 방금 | 12KB | `[▶ 실행]` `[🗑]` |

> 프로파일을 명시했는데 카탈로그에 등록되어 있지 않으면 412 응답 + 안내 — §5.1 부터 다시 또는 비로그인으로 비워둠.

### 5.4 첫 실행 + 결과 확인

**실행**

1. 위 표의 `[▶ 실행]` 클릭
2. 화면 가운데 `▶️ 실행` 카드가 활성화 — 진행 상황이 실시간으로 흐른다:

   ```
   14:23:01 [로그인 상태 확인] result=valid
   14:23:03 [step.goto]        /dashboard ✓
   14:23:05 [step.click]       "메뉴" ✓
   ...
   ```

3. 끝나면 `📊 결과` 카드에 한 줄 추가:

   | 시간 | 시나리오 | 결과 | |
   |---|---|---|---|
   | 14:23 오늘 | dashboard-tour | ✓ PASS | `[상세→]` |

**결과 검증**

`[상세→]` 클릭 → 화면이 좌우로 나뉜다:

- **좌측**: 스텝 리스트 (각 스텝 PASS / FAIL 표시)
- **우측**: 선택한 스텝의 스크린샷
- 스크린샷 클릭 → 원본 크기 lightbox 로 확대
- 실패한 스텝은 빨간 강조 + 실패 직전 화면 + Playwright 예외 메시지

**HTML 리포트**

상세 화면 우상단 `[📥 HTML 리포트]` 클릭 → 단일 HTML 파일 다운로드. 외부 PC 에 보내서 더블클릭만 해도 같은 결과를 볼 수 있다 (스크린샷 포함, 외부 의존성 없음).

---

## 6. 자동 운영 — 30분 주기

매번 사람이 `[▶ 실행]` 을 누르지 않고, OS 스케줄러가 30분마다 자동 실행하게 만들 수 있다.

### 6.1 스케줄러 등록

설치 시 `--register-task` 를 줬다면 설치 마지막에 다음 비슷한 안내문이 출력된다:

**Mac / Linux**

```
crontab 에 다음을 추가:
*/30 * * * * ~/.dscore.ttc.monitor/venv/bin/python -m monitor replay-script \
   ~/.dscore.ttc.monitor/scripts/dashboard-tour.py \
   --out ~/.dscore.ttc.monitor/runs/auto --profile dpg-qa
```

**Windows**

```
schtasks /create /sc minute /mo 30 /tn "Monitor Replay dashboard-tour" \
   /tr "%USERPROFILE%\.dscore.ttc.monitor\venv\Scripts\python.exe -m monitor replay-script \
        %USERPROFILE%\.dscore.ttc.monitor\scripts\dashboard-tour.py \
        --out %USERPROFILE%\.dscore.ttc.monitor\runs\auto --profile dpg-qa"
```

이걸 시나리오 `.py` 1개당 한 번씩 등록한다 (시나리오마다 별도 항목). 비로그인 시나리오면 `--profile` 인자 생략.

### 6.2 평소 — 사람 개입 X

스케줄러가 30분마다 실행한다. 결과는 `결과` 카드에 자동 누적. 정상인 동안은 화면을 안 봐도 된다.

### 6.3 알람 대응 — 만료되면

세션은 사이트 정책에 따라 몇 시간 ~ 며칠 만에 만료된다. 만료되면:

- 화면 헤더에 `🔴 1 만료` (빨간 배지)
- `로그인 프로파일` 카드의 해당 행이 `🔴 다시 로그인 필요`
- `결과` 카드의 다음 실행부터는 `⚠ 만료` 로 자동 표시 (실행은 안 시도 — 부작용 방지)

대응:

1. `로그인 프로파일` 카드 → 만료된 행의 `[↻ 다시 로그인]` 클릭
2. 사이트 주소 입력 (이전에 입력한 값이 prefill 됨) → 브라우저 창 자동으로 열림
3. 직접 로그인 → 창 닫기
4. 다음 스케줄 트리거에 자동으로 다시 통과.

---

## 7. 명령줄 (CLI) 사용

GUI 없이 같은 일을 명령으로 할 수 있다. 스크립트 자동화 / 헤드리스 무인 PC / GUI 가 안 뜨는 환경에서 쓴다.

| 명령 | 무엇 |
|---|---|
| `python -m monitor profile list` | 등록된 로그인 프로파일 목록 |
| `python -m monitor profile seed <이름> --target <URL>` | 새 프로파일 등록 (브라우저 열림) |
| `python -m monitor profile delete <이름>` | 프로파일 삭제 |
| `python -m monitor replay-script <.py 경로> --out <결과 폴더> [--profile <alias>] [--verify-url <URL>]` | 단일 .py 시나리오 1회 실행 (D17) |

**중요** — 위 명령의 `python` 은 반드시 설치 시 만든 가상환경의 Python 을 가리켜야 한다. 시스템 Python 으로 실행하면 모듈을 못 찾는다.

| OS | 가상환경 Python 경로 |
|---|---|
| Mac / Linux | `~/.dscore.ttc.monitor/venv/bin/python` |
| Windows | `%USERPROFILE%\.dscore.ttc.monitor\venv\Scripts\python.exe` |

### 7.1 종료 코드

`replay` 명령은 종료 시 다음 중 하나를 반환한다.

| 코드 | 뜻 | 운영자 액션 |
|---|---|---|
| 0 | 성공 (모든 스텝 PASS) | 없음 |
| 1 | 시나리오 스텝 실패 (인증 외 원인) | Replay UI `[상세→]` 로 결과 검증 |
| 2 | 시스템 오류 (zip 깨짐, 환경 비정상) | 엔지니어 조사 |
| 3 | **로그인 만료 또는 미등록** | `[↻ 다시 로그인]` |

CI / 외부 모니터링 시스템은 이 종료 코드로 분기한다.

---

## 8. 파일이 어디 저장되나

```
~/.dscore.ttc.monitor/                       (Mac/Linux. Windows 는 %USERPROFILE%\.dscore.ttc.monitor\)
│
├── venv/                                    Python 가상환경
├── chromium/                                Playwright 가 쓰는 브라우저
│
├── auth-profiles/                           로그인 상태 보관함
│   ├── _index.json                          ← 어떤 프로파일이 있는지 목록
│   ├── _index.lock                          ← 동시 접근 잠금 파일
│   └── <프로파일이름>.storage.json          ← 실제 로그인 상태 (쿠키 + localStorage)
│
├── scripts/                                 업로드된 시나리오 .py (D17)
│   └── <시나리오이름>.py
│
├── runs/                                    실행 결과 누적
│   └── <실행ID>/
│       ├── run_log.jsonl                    ← 이벤트 로그 (스텝별 결과)
│       ├── trace.zip                        ← Playwright 트레이스 (디버깅용)
│       ├── screenshots/                     ← 스텝별 PNG
│       ├── exit_code                        ← 종료 코드 1줄
│       └── meta.json                        ← 실행 메타 (시작/종료 시각 등)
│
├── replay-ui.stdout.log                     ← Replay UI 표준 출력 로그
└── replay-ui.stderr.log                     ← Replay UI 에러 로그 (Replay UI 가 안 뜨면 여기부터)
```

---

## 9. 보안 — 무엇이 어디로 안 나가나

| 항목 | 정책 |
|---|---|
| Replay UI 접근 범위 | **모니터링 PC 자기 자신에서만**. LAN 의 다른 PC 가 접속하면 거부 |
| 로그인 상태 (`*.storage.json`) | 모니터링 PC 의 보관함에만 있음. 시나리오 `.py` 안에는 절대로 안 들어감 |
| ID / 비밀번호 | 모니터링 PC 어디에도 평문으로 저장 안 함. 사람이 그때그때 브라우저에 직접 입력 |
| 시나리오 `.py` | 녹화 PC 의 다운로드 응답이 `auth_flow.sanitize_script` 통과 — 평문 비밀번호 placeholder 처리. 호스트 절대경로 / 로그인 상태 파일 미포함 |
| 실행 모드 | 사용자 startup task. OS 서비스 / 데몬 X (브라우저 GUI 를 띄우려면 사용자 세션이 필요) |

다른 PC 에서 결과를 봐야 하면 모니터링 PC 에서 `[📥 HTML 리포트]` 받아서 공유한다 (Replay UI 자체를 LAN 에 노출하지 않는다).

---

## 10. 자주 막히는 곳

| 증상 | 원인 / 해결 |
|---|---|
| 브라우저에서 `http://127.0.0.1:18094` 가 안 뜸 | [§4.6](#46-안-떴을-때--수동-기동) 의 `./replay-ui/run-replay-ui.sh restart` 시도 → 그래도 안 뜨면 `~/.dscore.ttc.monitor/replay-ui.stderr.log` 확인 |
| 업로드 후 `▶ 실행` 412 (프로파일 등록 필요) | 적용할 로그인 프로파일 select 에 입력한 alias 가 카탈로그에 없음. §5.1 부터 다시 또는 *비로그인* 으로 비워둠 |
| 로그인 했는데 매번 만료 알람 | verify URL 이 잘못 — 로그인 페이지로 잡혀 있으면 항상 만료로 판정. Replay UI 카드의 verify URL 입력에 *이미 로그인된 페이지* 의 URL 을 직접 명시 (또는 비워서 프로파일 카탈로그 fallback 사용) |
| 사이트가 1시간 만에 매번 만료 | 사이트 정책상 세션이 짧다. 일정 주기로 사람이 `[↻ 다시 로그인]` 하는 게 정상 동작. 자동 재로그인 안 한다 ([§1.2](#12-자동-재로그인은-안-한다)) |
| 같은 LAN 의 다른 PC 에서 접속 안 됨 | 의도된 보안 정책. 결과만 공유하려면 `[📥 HTML 리포트]` 받아서 이메일 / 메신저로 |
| Windows 에서 `[↻ 다시 로그인]` 시 브라우저가 안 열림 | Replay UI 가 OS 서비스로 잘못 등록된 상태. `install-monitor.cmd` 로 재설치 → 작업 스케줄러에 사용자 권한으로 다시 등록 |
| `python -m monitor` 가 `ModuleNotFoundError` | 시스템 Python 을 쓰고 있다. [§7](#7-명령줄-cli-사용) 의 가상환경 Python 경로 사용 |

---

## 11. 공유 모드 — 같은 호스트에서 Recording UI 와 로그인 프로파일 공유

> **언제 쓰는가** — 개발자 1 명이 같은 PC 에서 Recording UI 와 Replay UI 를 둘 다
> 띄우는 경우. 같은 사이트에 두 번 (Recording 시드 + Replay 시드) 로그인하는 게
> 낭비라면 두 UI 가 같은 `auth-profiles/` 디렉토리를 보게 만들 수 있다.
>
> **언제 쓰지 말 것** — Recording PC 와 Monitoring PC 가 *물리적으로 다른 머신*
> 인 폐쇄망 운영 모델. 그쪽에서는 모니터링 PC 가 자체적으로 로그인하는 게 원래
> 설계 ([§1.1](#11-이-도구가-푸는-문제)) — 공유하지 말 것.

### 11.1 기본 경로 (서로 다름)

| | 기본 경로 |
|---|---|
| Recording UI | `~/ttc-allinone-data/auth-profiles/` |
| Replay UI    | `~/.dscore.ttc.monitor/auth-profiles/` |

파일 포맷 (`<프로파일이름>.storage.json`, `_index.json`, `_index.lock`) 은 두 쪽이
동일하게 `zero_touch_qa.auth_profiles` 모듈을 쓰므로 **완전 호환**. 디렉토리만 같이
가리키면 그대로 공유된다.

### 11.2 공유하는 법 — `AUTH_PROFILES_DIR` env 통일

두 launcher 모두 `AUTH_PROFILES_DIR` env 를 존중한다. 쉘에서 한 줄 export 후 띄우면 끝.

**Mac / Linux**

```bash
export AUTH_PROFILES_DIR="$HOME/ttc-allinone-data/auth-profiles"

# 그 다음 두 UI 띄우기 (어느 쪽이든 같은 env 가 적용됨)
./playwright-allinone/recording-ui/run-recording-ui.sh restart
./playwright-allinone/replay-ui/run-replay-ui.sh restart
```

**Windows (PowerShell)**

```powershell
$env:AUTH_PROFILES_DIR = "$HOME\ttc-allinone-data\auth-profiles"

bash playwright-allinone/recording-ui/run-recording-ui.sh restart
bash playwright-allinone/replay-ui/run-replay-ui.sh restart
```

위와 같이 띄운 뒤 Recording UI 에서 `dpg-qa` 로 시드하면, Replay UI 의 `🔐 로그인
프로파일` 카드에 `dpg-qa` 가 자동으로 보인다 (그 반대도 동일).

### 11.3 한 번만 옮기고 싶다면 — 디렉토리 복사

이미 한쪽에 등록된 프로파일을 다른 쪽에 가져가고 싶다면 디렉토리째 복사하면 된다.

```bash
# Recording UI → Replay UI 방향 (예시)
rsync -a "$HOME/ttc-allinone-data/auth-profiles/" \
        "$HOME/.dscore.ttc.monitor/auth-profiles/"
```

`_index.lock` 파일은 활성 잠금이라 양쪽 UI 가 떠 있을 때 복사하면 잠금 충돌이
날 수 있다 — 복사 전 한 쪽은 `stop` 권장.

### 11.4 주의

- **세션 / 토큰 노출 범위 변화** — 공유하면 Recording UI 에서 쓰는 동일 토큰을
  Replay UI 도 그대로 쓴다. 정상이지만, 두 UI 가 다른 권한·계정 컨텍스트라고
  가정한 운영 정책이 있다면 깨진다.
- **동시 시드 금지** — 같은 프로파일 이름으로 두 UI 에서 동시에 시드 (브라우저
  창 2 개 동시 열림) 하면 `_index.lock` 경합. 이 경우 둘 다 실패할 수 있다.
  시드는 한 쪽에서만.
- **만료 알람** — 만료 감지는 verify URL 단위라 양쪽 UI 가 같은 만료 시점을 본다.
  한 쪽에서 재로그인하면 다른 쪽에도 그대로 반영됨 (의도된 동작).

---

## 12. 다음 문서

| 알고 싶은 것 | 문서 |
|---|---|
| 큰 그림 (녹화 PC ↔ 모니터링 PC) | [README](../README.md) |
| 시나리오 .py 받기 (녹화 PC 쪽) | [recording-ui.md](recording-ui.md) |
| 통합 테스트 결과 / 매트릭스 | [replay-ui-integration-tests.md](replay-ui-integration-tests.md) |
| 설계 결정 배경 | `.claude/plans/squishy-wishing-emerson.md` |
