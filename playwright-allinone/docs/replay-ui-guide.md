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
| **휴대용 패키지** | Replay UI 휴대용 zip. 파일명 `DSCORE-ReplayUI-portable-{win64\|macos-arm64}-<날짜시각>.zip`. Python 인터프리터 / 의존 패키지 / Chromium / 소스가 모두 들어 있어 받는 사람은 zip 풀고 더블클릭만 (설치 없음, 인터넷·관리자권한 불요) |

---

## 3. 받기 전에 확인할 것

받는 PC 가 다음만 맞으면 됩니다.

| 항목 | 필요 |
|---|---|
| OS | Windows 10·11 (64비트) **또는** macOS 12 이상 (Apple Silicon) |
| 디스크 여유 공간 | 1GB 이상 |
| 권한 | 일반 사용자 (관리자 권한 / sudo 불필요) |
| 인터넷 | **불필요** — 폐쇄망 PC OK |
| 미리 깔 것 | **없음** — Python · Chromium · 모든 라이브러리가 zip 안에 동봉됨 |

> 회사 PC 라 관리자 권한이 막혀 있어도 됩니다. 외부 인터넷이 차단된 폐쇄망 PC 도 됩니다.

---

## 4. 설치·기동 — 5단계, 5분

다음 다섯 단계를 순서대로 따라하면 끝납니다. 각 단계 안에 「**막혔다면**」 박스가 있으니, 안 되면 그것부터 보세요.

### 단계 1 — zip 파일 받기

운영팀에서 보내준 zip 파일을 받습니다. **본인 OS 와 맞는 파일** 인지 확인:

| 본인 OS | 파일 이름 (예) |
|---|---|
| Windows | `DSCORE-ReplayUI-portable-win64-<날짜>.zip` |
| macOS | `DSCORE-ReplayUI-portable-macos-arm64-<날짜>.zip` |

받는 곳은 사내 공유 폴더 / 이메일 / USB / GitHub Release 등 — 어디서든 OK.

**확인** — 파일 크기가 약 360MB 인지 본다. 한참 작으면 다운로드가 끊긴 것 → 다시 받기.

> ⚠️ **OS 가 다른 zip 은 절대 안 됩니다.** Windows PC 에 macos zip 을 받으면 단계 3 에서 안 떠요.

### 단계 2 — 폴더에 풀기

받은 zip 을 자기 PC 의 임의의 폴더에 풉니다. 추천 위치:

| OS | 추천 위치 |
|---|---|
| Windows | 바탕화면 또는 `D:\` (경로가 짧을수록 좋음) |
| macOS | `~/Desktop/` 또는 `~/Documents/` |

**푸는 방법**:

- **Windows** — 받은 zip 우클릭 → `압축 풀기...` → 위치 지정 → `압축 풀기` 클릭. (별도 도구 필요 없음, OS 기본 기능.)
- **macOS** — 받은 zip 더블클릭. 같은 폴더에 자동으로 폴더가 생깁니다.

풀고 나면 안에 다음 파일들이 보여야 합니다:

```text
replay-ui/
├── Launch-ReplayUI.bat       ← Windows: 이거 더블클릭
├── Stop-ReplayUI.bat         ← Windows: 종료할 때
├── Launch-ReplayUI.command   ← macOS: 이거 더블클릭
├── Stop-ReplayUI.command     ← macOS: 종료할 때
├── README.txt
└── (다른 폴더들 — 손대지 마세요)
```

> 푼 폴더는 나중에 옮기거나 이름 바꿔도 OK. USB 로 다른 PC 로 옮겨 그대로 실행해도 됩니다.

**막혔다면**:

- *"경로가 너무 깁니다"* (Windows) → 더 짧은 경로 (`D:\replay-ui\`) 에 풀기.
- 압축 안 푼 채 zip 안에서 .bat 을 그냥 더블클릭 → 동작 안 함. *반드시 압축 풀기 먼저.*

### 단계 3 — 실행

#### Windows

`replay-ui\Launch-ReplayUI.bat` **더블클릭**.

1. 검은 콘솔 창이 잠깐 떴다가 최소화됩니다.
2. 약 10~15초 후 기본 브라우저가 자동으로 열립니다.

**Windows Defender / SmartScreen 경고가 뜨면**:

- *"Windows 의 PC 보호"* 같은 파란 창 → `추가 정보` 클릭 → `실행` 버튼 클릭.
- 회사 정책으로 강하게 막혀 있으면 IT 팀에 *"이 .bat 파일 실행 허용"* 요청.

#### macOS

`replay-ui/Launch-ReplayUI.command` **더블클릭**.

**최초 1회만** 다음 우회가 필요합니다 (Apple 승인 안 받은 사내 배포라서).

1. 처음 더블클릭하면 *"확인되지 않은 개발자... 열 수 없습니다"* 경고 → `취소` 누름.
2. Finder 에서 같은 파일을 **Control 키 누른 채 클릭** (또는 우클릭) → 메뉴에서 `열기` 선택.
3. *"... 정말 열겠습니까?"* 다이얼로그 → `열기` 클릭.
4. Terminal 창이 뜨고, 약 10~15초 후 기본 브라우저가 자동으로 열립니다.

> 한 번 위 *Control-클릭→열기* 를 하면 다음부터는 그냥 더블클릭으로 바로 됩니다.

**막혔다면**:

- 30초 기다려도 브라우저가 안 열림 → 단계 4 의 직접 접속 방법으로 시도.
- 그래도 안 됨 → `replay-ui/data/runs/replay-ui.stderr.log` 파일을 메모장 / TextEdit 으로 열어 *마지막 10줄* 을 운영팀에 전달.

### 단계 4 — 작동 확인

브라우저에 다음이 보이면 성공입니다.

- 주소창 — `http://127.0.0.1:18094/`
- 화면 상단 — `🎬 Replay UI`
- 카드 4개 — `🔐 로그인 프로파일`, `📄 시나리오 스크립트`, `▶️ 실행`, `📊 결과`

브라우저가 자동으로 안 열린 경우 — 직접 다음 주소를 브라우저 주소창에 입력하세요:

```text
http://127.0.0.1:18094/
```

> ⚠️ 이 주소는 **그 PC 자기 자신** 에서만 열립니다. 같은 사무실의 다른 PC 가 IP 로 접속해도 거부됩니다. *의도된 보안 정책* — 결과를 다른 사람과 공유하려면 [§9 보안](#9-보안--무엇이-어디로-안-나가나) 의 HTML 리포트 다운로드를 사용하세요.

### 단계 5 — 종료할 때

- **Windows** — `replay-ui\Stop-ReplayUI.bat` 더블클릭.
- **macOS** — `replay-ui/Stop-ReplayUI.command` 더블클릭.

종료 후 데이터 (등록한 로그인 프로파일, 실행 결과) 는 그대로 보존됩니다. 다음에 다시 단계 3 부터 하면 그 자리에서 이어집니다.

> 단축 종료 — 검은 콘솔 / Terminal 창의 X 버튼으로 닫아도 됩니다 (콘솔 창은 단계 3 에서 떴던 그것).

### 그 외 자주 일어나는 일

| 증상 | 어떻게 |
|---|---|
| 단계 3 더블클릭했는데 콘솔 창이 잠깐 뜨고 그냥 사라짐 | 이미 같은 PC 에 Replay UI 가 떠 있는 상태. 단계 4 의 주소를 직접 입력하면 바로 그 인스턴스에 접속됩니다. |
| 단계 3 후 1분 기다려도 브라우저 안 뜸 | `replay-ui/data/runs/replay-ui.stderr.log` 가 있으면 그 파일 마지막 10줄 운영팀에 전달. 파일 자체가 없으면 단계 3 의 더블클릭이 *완전히 차단된 것* — IT 팀에 .bat / .command 실행 허용 요청. |
| 종료 후 다시 더블클릭하니 *"포트 18094 사용 중"* 같은 메시지 | 이전 인스턴스가 안 죽고 살아있음. 단계 5 한 번 누르고 다시 단계 3. |

자세한 자주 막히는 케이스는 [§10](#10-자주-막히는-곳).

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

- 브라우저가 안 열린다 → `data/runs/replay-ui.stderr.log` 에 `SeedSubprocessError` 또는 `ChipsNotSupportedError` 가 있으면 휴대용 빌드 산출이 잘못된 것 (Plan A 변경이 빠진 빌드). 새로 zip 을 받거나 빌드 머신에서 `pack-*` 재실행.
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

zip 풀린 폴더 절대경로를 `<ROOT>` 라 하면 (예: `D:\ReplayUI\replay-ui` 또는 `/Volumes/USB/ReplayUI-macOS/replay-ui`):

**macOS** (crontab)

```cron
*/30 * * * * <ROOT>/python/bin/python3 -m monitor replay-script \
   <ROOT>/data/scripts/dashboard-tour.py \
   --out <ROOT>/data/runs/auto --profile dpg-qa
```

**Windows** (작업 스케줄러)

```bat
schtasks /create /sc minute /mo 30 /tn "Monitor Replay dashboard-tour" \
   /tr "<ROOT>\embedded-python\python.exe -m monitor replay-script \
        <ROOT>\data\scripts\dashboard-tour.py \
        --out <ROOT>\data\runs\auto --profile dpg-qa"
```

이걸 시나리오 `.py` 1개당 한 번씩 등록한다 (시나리오마다 별도 항목). 비로그인 시나리오면 `--profile` 인자 생략.

스케줄러 등록 시 child process 가 휴대용 자산을 찾도록 다음 env 도 같이 박아야 한다 (`schtasks` 는 직접 env 못 박으므로 cmd 안에 chain 으로): `PYTHONPATH=<ROOT>;<ROOT>\site-packages`, `PLAYWRIGHT_BROWSERS_PATH=<ROOT>\chromium`, `MONITOR_HOME=<ROOT>\data`, `AUTH_PROFILES_DIR=<ROOT>\data\auth-profiles`. macOS 도 동등 (sep `:`).

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

**중요** — 위 명령의 `python` 은 반드시 zip 폴더 안의 embedded python 을 가리켜야 한다. 시스템 Python 으로 실행하면 모듈을 못 찾는다.

| OS | embedded python 경로 |
|---|---|
| macOS | `<ROOT>/python/bin/python3` |
| Windows | `<ROOT>\embedded-python\python.exe` |

`<ROOT>` = zip 풀린 `replay-ui/` 폴더 절대경로.

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

**모든 데이터가 zip 풀린 `replay-ui/` 폴더 안 `data/` 에 저장된다.** 호스트 홈 디렉토리는 건드리지 않는다. 폴더 자체를 USB 로 옮기면 데이터까지 같이 따라간다.

```text
<ROOT>/                                      ← zip 풀린 replay-ui/ 폴더 절대경로
│
├── embedded-python/                         ← Python 3.11 (Windows)
├── python/                                  ← Python 3.11 (macOS)
├── site-packages/                           ← fastapi · uvicorn · playwright · pywin32 …
├── chromium/                                ← Playwright Chromium
├── replay_service/ · monitor/ · recording_shared/ · zero_touch_qa/
│
└── data/                                    ← 실행하면서 누적되는 모든 사용자 상태
    ├── auth-profiles/
    │   ├── _index.json                      ← 등록된 프로파일 목록
    │   ├── _index.lock                      ← 동시 접근 잠금 파일
    │   └── <프로파일이름>.storage.json      ← 실제 로그인 상태 (쿠키 + localStorage)
    │
    ├── scripts/                             ← 업로드된 시나리오 .py
    │   └── <시나리오이름>.py
    │
    ├── runs/                                ← 실행 결과 누적
    │   ├── <runId>/
    │   │   ├── run_log.jsonl                ← 이벤트 로그 (스텝별 결과)
    │   │   ├── trace.zip                    ← Playwright 트레이스
    │   │   ├── screenshots/                 ← 스텝별 PNG
    │   │   ├── exit_code                    ← 종료 코드 1줄
    │   │   └── meta.json                    ← 실행 메타 (시작/종료 시각)
    │   ├── replay-ui.stdout.log             ← Replay UI 표준 출력
    │   └── replay-ui.stderr.log             ← Replay UI 에러 로그 (UI 가 안 뜨면 여기부터)
    │
    └── scenarios/                           ← (예약)
```

> Recording UI 와의 공유는 휴대용 모델에서 *지원하지 않는다*. 다른 PC 의 Recording UI 에서 만든 시나리오 `.py` 를 받아 본 폴더에 업로드하는 흐름만 사용한다 (§5.2~§5.4).

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
| 브라우저에서 `http://127.0.0.1:18094` 가 안 뜸 | `<ROOT>/data/runs/replay-ui.stderr.log` 의 첫 traceback 확인. 포트 충돌이면 launcher 가 기존 인스턴스로 브라우저 열어줌. |
| 업로드 후 `▶ 실행` 412 (프로파일 등록 필요) | 적용할 로그인 프로파일 select 에 입력한 alias 가 카탈로그에 없음. §5.1 부터 다시 또는 *비로그인* 으로 비워둠 |
| 로그인 했는데 매번 만료 알람 | verify URL 이 잘못 — 로그인 페이지로 잡혀 있으면 항상 만료로 판정. Replay UI 카드의 verify URL 입력에 *이미 로그인된 페이지* 의 URL 을 직접 명시 (또는 비워서 프로파일 카탈로그 fallback 사용) |
| 사이트가 1시간 만에 매번 만료 | 사이트 정책상 세션이 짧다. 일정 주기로 사람이 `[↻ 다시 로그인]` 하는 게 정상 동작. 자동 재로그인 안 한다 ([§1.2](#12-자동-재로그인은-안-한다)) |
| 같은 LAN 의 다른 PC 에서 접속 안 됨 | 의도된 보안 정책. 결과만 공유하려면 `[📥 HTML 리포트]` 받아서 이메일 / 메신저로 |
| `python -m monitor` 가 `ModuleNotFoundError` | 시스템 Python 을 쓰고 있다. [§7](#7-명령줄-cli-사용) 의 embedded python 경로 사용 |

---

## 11. 데이터 위치 정책

휴대용 모델은 모든 데이터를 zip 풀린 `replay-ui/data/` 안에만 둔다. 호스트의 홈 디렉토리도, Recording UI 와의 공유 디렉토리도 사용하지 않는다.

### 11.1 카탈로그 위치

| | 경로 |
|---|---|
| 휴대용 폴더 안 | `<ROOT>/data/auth-profiles/` |

`Launch-ReplayUI.{bat,command}` 가 `AUTH_PROFILES_DIR` 를 *unconditional* 로 위 경로로 박는다. 호출자 환경변수로 override 안 됨 (정책상 의도).

### 11.2 공유가 필요하면

같은 사이트의 로그인을 여러 PC 에서 공유하려면 *카탈로그 파일을 직접 복사* 한다 — `<ROOT>/data/auth-profiles/_index.json` 과 `<프로파일>.storage.json` 파일들. 다만 storage 안의 쿠키는 도메인 / 디바이스 fingerprint 따라 유효성이 갈리므로 *재시드가 더 안전*.

### 11.3 폐쇄망 운영 모델

폐쇄망 정식 운영에서는 Recording PC 와 Monitoring PC 가 *물리적으로 다른 머신*. 각 PC 가 자체 로컬 카탈로그를 사용하며 이게 원래 설계 ([§1.1](#11-이-도구가-푸는-문제)). 휴대용 모델은 `<ROOT>/data/auth-profiles/` 가 그 머신 안에서만 닫혀 있어 같은 정책을 자연스럽게 만족한다.

---

## 12. 휴대용 zip 만들기 (빌드 머신용)

**받는 사람은 이 절을 읽지 않습니다.** 새 zip 산출물을 만드는 *빌드 머신* (운영팀 / 개발자) 용입니다. 받는 사람은 §1~§10 까지로 충분.

### 12.1 한 번 — 캐시 채우기

빌드 머신에서 처음 한 번 인터넷에 연결된 채로:

```bash
bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target win64
# macOS arm64 도 만들 거면:
bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target macos-arm64
# 둘 다 한 번에:
bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target all
```

`.replay-ui-cache/cache/` 안에 wheels + Chromium 이 채워집니다.

### 12.2 zip 산출

```powershell
# Windows 빌드 머신 (PowerShell)
powershell -NoProfile -ExecutionPolicy Bypass `
  -File playwright-allinone/replay-ui-portable-build/pack-windows.ps1 -MakeZip
```

```bash
# macOS arm64 빌드 머신
bash playwright-allinone/replay-ui-portable-build/pack-macos.sh --make-zip
```

산출 위치 — `playwright-allinone/replay-ui-portable-build/build-out/DSCORE-ReplayUI-portable-{win64|macos-arm64}-<날짜시각>.zip` (약 360MB).

### 12.3 자동 갱신 hook (개발자 권장)

저장소 코드를 바꾼 채로 `git push` 하면 자동으로 자산을 재채워주는 hook. 한 번만:

```bash
git config core.hooksPath .githooks
```

설치 후 `git push` 시 stale 검출 + `pack-*` 자동 호출. 실패해도 push 자체는 차단 안 함 (개발 흐름 보호). 자세한 동작은 [.githooks/README.md](../../.githooks/README.md).

---

## 13. 다음 문서

| 알고 싶은 것 | 문서 |
|---|---|
| 큰 그림 (녹화 PC ↔ 모니터링 PC) | [README](../README.md) |
| 시나리오 .py 받기 (녹화 PC 쪽) | [recording-ui.md](recording-ui.md) |
| 통합 테스트 결과 / 매트릭스 | [replay-ui-integration-tests.md](replay-ui-integration-tests.md) |
| 설계 결정 배경 | `.claude/plans/squishy-wishing-emerson.md` |
