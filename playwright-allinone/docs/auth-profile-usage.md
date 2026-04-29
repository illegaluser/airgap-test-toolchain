# Auth Profile 사용 가이드 — 네이버 OAuth 연동 서비스 E2E 테스트

> 이 문서는 **네이버로 로그인되는 외부 서비스** (예: 사내 예약 시스템, 커머스 등)
> 를 E2E 테스트할 때 *사람이 1회 통과한 네이버 로그인 결과를 storageState 로
> 저장해두고 이후 녹화/재생에서 자동 재사용* 하는 방법을 다룬다.
>
> 설계 배경: [PLAN_AUTH_PROFILE_NAVER_OAUTH.md](PLAN_AUTH_PROFILE_NAVER_OAUTH.md)
> 다른 인증(`auth_login` DSL — form / TOTP) 은 [auth-login-usage.md](auth-login-usage.md)
> 참조. 본 시스템은 그것의 *대체*가 아니라 *보완* — IdP 화면 자체를 자동화로
> 통과시키는 게 불가능한 케이스(네이버 등) 용.

---

## 0. 한 줄 요약

```
"네이버는 통과점, 진짜 대상은 연동 서비스" — 사람이 1회 시드 → 이후 자동.
```

| 사용자 부담 | 빈도 |
|---|---|
| 네이버 ID/PW + 2중 확인 직접 통과 | **시드 1회 + 만료 시 재시드 (≈ 일 1회 floor)** |
| 일상 녹화 / 재생 (대상 서비스 기능) | n회 — *대부분의 경우* 네이버 화면 등장 0번 |

> **"네이버 화면 0번" 보장이 아님**. 만료·환경 변경·서비스 측 reauth 강제·sessionStorage 의존 등으로 네이버 왕복이 다시 트리거될 수 있다. 이런 경우는 빠르게 감지해서 재시드로 유도.

---

## 1. 현재 구현 상태 (2026-04-29)

### 1.1 백엔드 / API

| Phase | 영역 | 상태 |
|---|---|---|
| P1 | `zero_touch_qa.auth_profiles` 모듈 — 카탈로그 / dump 검증 / verify / seed 라이프사이클 | ✅ |
| P2 | CLI — `python3 -m zero_touch_qa auth seed/list/verify/delete` | ✅ |
| P3 | Recording 서버 통합 — `/auth/profiles*` 5 endpoint + `/recording/start` 게이트 + `original.py` 경로 portabilize | ✅ |
| P4 | 재생 자동 매칭 — `metadata.json.auth_profile` → `--storage-state-in` + fingerprint env 자동 주입 | ✅ |
| P5 | UI — 인증 블록 + 모달 4개 (시드 / 시드 진행 / 만료 / 머신 불일치) + 결과 카드 메타 + 세션 테이블 컬럼 | ✅ |

### 1.2 의사결정 일람 (D1~D16)

| D# | 결정 |
|---|---|
| D1 | 방식 = storageState 재사용만 (callback mock / 테스트 세션 API 제외) |
| D2 | 시드 도구 = `playwright open --save-storage` |
| D3 | `original.py` storage 경로 = env var 치환 |
| D4 | 시드 파일 위치 = `~/ttc-allinone-data/auth-profiles/` |
| D5 | 자동 verify = Start 직전 항상 실행 |
| D6 | 네이버는 통과점 — 대상은 연동 서비스 |
| D7 | 목표 사용자 = 개인 QA, 단일 머신, 며칠 주기 회귀 |
| D8 | 재시드 cadence 기대치 = "일 1회 floor" |
| D9 | 재생도 headed 강제 (운영 기본) |
| D10 | fingerprint pin = viewport / locale / timezone / color_scheme + Playwright 버전·채널 (UA 제외) |
| D11 | 머신 결속 — 시드 머신 ≠ 재생 머신 검출 시 빨간 경고 (차단 X) |
| D12 | dump 검증 — 양 도메인 쿠키 강제 |
| D13 | verify = service-side authoritative + naver-side optional weak probe |
| D14 | CHIPS guard = Playwright ≥1.54 게이트 |
| D15 | scenario.json 형식 미수정 (auth_profile 메타는 `metadata.json` 에 저장) |
| D16 | sessionStorage detection — 함수(`detect_session_storage_use`)만 구현. *seed 파이프라인 통합은 후속 트랙* (`playwright open` 의 close-time hook 부재). 카탈로그를 외부에서 직접 편집해 `session_storage_warning=True` 로 박은 경우만 UI 라벨 노출 |

### 1.3 E2E 테스트 슈트

| Tier | 파일 | 검증 영역 | 케이스 | 시간 |
|---|---|---|---:|---:|
| 1 | `test/e2e_p1_auth_profiles.py` (standalone script) | auth_profile 풀 사이클 (시뮬 시드 → verify → 만료) | 9 단계 | ~10s |
| 2 | `test/test_auth_profile_api_e2e.py` | HTTP API + recording_start 통합 + 만료 분기 | 7 | ~5s |
| 3 | `test/test_auth_profile_ui_e2e.py` | 인증 블록 + 모달 4개 + 만료/재시드 동선 | 11 | ~16s |

전체 27 케이스. **모두 PASS** 상태.

---

## 2. 시작하기 전에 — 운영 권고

### 2.1 테스트 계정 확보

> **운영 계정 사용 금지.** 테스트 전용 네이버 계정 권장.

이유:
- 시드 단계에서 storage 파일에 세션 토큰이 저장됨 (mode 0600 강제)
- 자동화 IP/지문이 누적되면 네이버가 의심 활동으로 잠글 수 있음
- 재시드 시 동일 계정으로 반복 로그인 → CAPTCHA 빈도↑

권장 사양:
- 시드를 수행할 머신에서 *처음부터* 만든 신규 계정
- 2중 확인 SMS 수신 가능한 휴대폰 등록
- 가능하면 기기 등록 / 신뢰 환경 추가까지 미리 1회 (시드 시 추가 챌린지 빈도↓)

### 2.2 머신 환경

| 항목 | 권장 |
|---|---|
| Playwright | **≥1.54** (CHIPS partition_key 보존) — 본 시스템이 자동 검사 후 거절 |
| Chromium | Playwright 와 함께 설치된 버전 (channel = "chromium" 기본) |
| OS | macOS / Linux (POSIX, fcntl 사용) |
| display | 시드/녹화/재생 모두 headed 가 기본 (D9). headless 환경은 `AUTH_PROFILE_VERIFY_HEADLESS=1` 로 verify 만 옵트인 |
| 시드 머신 = 재생 머신 | **권장**. 다른 머신 사용 시 머신 불일치 모달이 노출되지만 차단은 안 함 |

### 2.3 디렉토리 / 권한

```
~/ttc-allinone-data/auth-profiles/        (mode 0700)
├── _index.json                           # 카탈로그 (mode 0600)
├── _index.lock                           # advisory flock (자동 생성)
└── <name>.storage.json                   # storageState dump (mode 0600)
```

`AUTH_PROFILES_DIR` env 로 override 가능 (테스트 격리 등).

### 2.4 GitIgnore

```gitignore
auth-profiles/
*.storage.json
```

storage 파일은 인증 토큰을 포함 — *절대 커밋 / 백업 / CI artifact 에 포함하지 말 것*.

---

## 3. UI 워크플로우 — 단계별 클릭 가이드

### 3.1 Recording UI 진입

```bash
# 데몬 실행 (첫 1회 — 평소엔 supervisord 가 띄움)
PYTHONPATH=. python3 -m uvicorn recording_service.server:app \
    --host 0.0.0.0 --port 18092
```

브라우저로 `http://localhost:18092/` 접속.

### 3.2 처음 사용 — 시드 (인생 1번)

> 가공 예시: **사내 예약 시스템 `booking.example.com` (네이버로 로그인 지원)**.

#### Step 1. "새 녹화 시작" 카드의 **인증 (선택)** 블록 확인

```
[Recording UI 메인]

새 녹화 시작
─────────────────────────────────────────
target_url       : [_______________________]
planning_doc_ref : [_______________________]

인증 (선택) — 시드된 storageState 재사용
  Auth Profile  : [(없음 — 비로그인 녹화) ▼]
                  [↻ verify (disabled)]
  — 프로파일을 선택하거나 새로 시드하세요 —
  [+ 새 세션 시드]

[▶ Start Recording]   [📁 Play Script from File]
```

#### Step 2. **[+ 새 세션 시드]** 클릭

다음 모달이 열림:

```
┌── 새 인증 세션 만들기 ─────────────────────────────────────┐
│ 이 단계는 1회만 수행합니다 (만료 시 재시드).                │
│                                                            │
│ 이름        : [booking-via-naver___________]               │
│ 시작 URL    : [https://booking.example.com/_______]        │
│   ↳ ⚠️ 테스트 대상 *서비스* 진입 페이지                    │
│      (네이버 로그인 페이지가 아닙니다!)                    │
│ 검증 URL    : [https://booking.example.com/mypage_]        │
│ 검증 텍스트 : [김QA님 환영합니다__________________] (선택) │
│ TTL 힌트    : [12] 시간   ⓘ 일 1회 재시드 가정 권장        │
│ ☑ 네이버 측 weak probe 활성 (best-effort, 권장)           │
│                                                            │
│ ⚠ 운영 계정 사용 금지 — 테스트 전용 계정 권장              │
│                            [열기 →]   [취소]               │
└────────────────────────────────────────────────────────────┘
```

> **자주 헷갈리는 지점**: "시작 URL" 은 네이버 로그인 페이지가 아니라 **테스트 대상 서비스의 진입 페이지** 다. 네이버는 단지 거쳐가는 곳.

각 필드:
| 필드 | 입력 예 | 설명 |
|---|---|---|
| 이름 | `booking-via-naver` | 영문/숫자/`_`/`-` 1~64자, 첫 글자는 영문/숫자 |
| 시작 URL | `https://booking.example.com/` | 사용자가 "네이버로 로그인" 버튼을 누를 화면 |
| 검증 URL | `https://booking.example.com/mypage` | 로그인된 사용자만 볼 수 있는 페이지 |
| 검증 텍스트 | `김QA님 환영합니다` | 선택. 입력하면 해당 문구까지 확인하는 강한 검증. 비우면 검증 URL 접근 성공만 확인 |
| TTL 힌트 | `12` (시간) | UI 표시용 만료 추정값 — 실제 만료는 verify 가 결정 |
| 네이버 weak probe | ☑ 활성 | best-effort 검증 추가. ok 판정에 영향 없음. 디버깅 도움 |

**[열기 →]** 클릭.

#### Step 3. 별도 브라우저 창이 뜸

UI 가 진행 패널로 전환:

```
🪟 로그인 세션 저장 중

다음을 직접 수행하세요:
  ① booking.example.com 페이지의 [네이버로 로그인] 버튼 클릭
  ② 네이버 로그인 화면에서 ID / 비밀번호 입력
  ③ 2중 확인 (SMS 등) 직접 통과
  ④ booking.example.com 으로 자동 복귀 → 검증 대상 페이지 확인
  ⑤ 로그인 완료 화면을 확인한 뒤 열린 브라우저 창을 닫으세요

상태: ⏳ 로그인 창 대기 중 — 창이 닫히면 세션 저장 후 검증 진행
```

이 시점에 PC 화면에 **별도의 Chromium 창** 이 떴다. 본 Recording UI 와는 *별개의 창* 이고, `booking.example.com` 의 첫 화면이 노출된다.

#### Step 4. 별도 창에서 직접 로그인

```
[별도 창 — booking.example.com]
  로그인 방법:
  [구글로 로그인] [카카오로 로그인] [네이버로 로그인]   ← 클릭
                                       ↓
[별도 창 — nid.naver.com]
  ID:  tester01      PW:  ********    [로그인]
                                       ↓
[별도 창 — 네이버 2중 확인 (SMS)]
  인증번호 [____]      [확인]
                                       ↓
[별도 창 — booking.example.com 메인]
  김QA님 환영합니다 ✓                  ← 본인 이름 확인 후 [X] 닫기
```

#### Step 5. 백엔드가 자동 처리

창이 닫히는 순간:

1. Playwright 가 storage 자동 dump → `~/ttc-allinone-data/auth-profiles/booking-via-naver.storage.json`
   - cookies (`.naver.com` + `.booking.example.com` 둘 다) + localStorage + IndexedDB
   - mode 0600
2. **dump 검증 (D12)** — 양 도메인 쿠키가 모두 있는지 확인. 누락 시 시드 실패 + 파일 삭제.
3. **자동 verify (D13)** — 새 Chromium 컨텍스트에 storage 적용 → 검증 URL 이동 → "김QA님" 텍스트 확인. headed 모드에서는 페이지를 잠시 유지해 사용자가 눈으로 확인할 수 있게 한다.
4. **카탈로그 등록** — 카탈로그에 새 항목, fingerprint capture, machine_id 박힘.

UI:

```
✅ 시드 완료 — 프로파일 "booking-via-naver"
이번 녹화에 사용할지 선택하세요. 사용하지 않아도 프로파일은 목록에 저장됩니다.
[사용하지 않음] [이 프로파일 사용]
```

`이 프로파일 사용` 버튼을 누르면 모달이 닫히고 메인 폼의 드롭다운에 새 프로파일이 선택됨.
`사용하지 않음` 을 누르면 프로파일은 카탈로그에 남지만 이번 녹화의 Auth Profile 선택은 비워둔다.

```
인증 (선택)
  Auth Profile  : [booking-via-naver — booking.example.com ▼]   [↻ verify]
                  ✓ verify OK · 방금 검증
                  [+ 새 세션 시드]
```

### 3.3 녹화 시작 (시드된 프로파일 사용)

#### Step 1. target_url + Auth Profile

```
target_url    : [https://booking.example.com/booking/new]
Auth Profile  : [booking-via-naver ▼]   ← 시드된 프로파일 선택
                ✓ verify OK · 방금 검증
```

#### Step 2. **[▶ Start Recording]** 클릭

백엔드:

1. **자동 verify** (D5) — 만료/머신 불일치 검사
2. **codegen 시작** — `playwright codegen` 에 `--load-storage` + fingerprint args 자동 주입

별도 codegen 창이 뜸:

```
🪟 codegen 창 — 이미 booking.example.com 에 로그인된 상태로
   회의실 예약 페이지 직접 노출. 네이버 화면 안 거침.
```

#### Step 3. 액션 수행 → **[■ Stop & Convert]**

평소 codegen 사용과 동일.

#### Step 4. 결과 확인

결과 카드에 인증 프로파일이 명시적으로 노출:

```
[결과]
  세션 ID         : abc123def456
  state           : ✓ done
  step 수         : 6
  인증 프로파일   : booking-via-naver
```

세션 메타 (`<session>/metadata.json`) 에 `auth_profile` 필드 저장됨. **scenario.json 자체는 수정되지 않음** (D15 — list 형식 유지).

### 3.4 재생

#### A. ▶ LLM 적용 코드 실행 (zero_touch_qa executor)

R-Plus 섹션의 **▶ Play → LLM 적용 코드 실행**:

1. 백엔드: 세션 metadata.json 에서 `auth_profile` 읽음
2. **자동 verify** — 만료 검사
3. `python3 -m zero_touch_qa --mode execute --scenario .../scenario.json --storage-state-in <storage>`
4. fingerprint env (`PLAYWRIGHT_VIEWPORT/LOCALE/TIMEZONE/COLOR_SCHEME`) 도 자동 주입

#### B. ▶ 테스트코드 원본 실행

`original.py` 가 **자동으로 portabilize 되어 있음** (D3, P3.10):

```python
# Before (codegen 출력 원본)
context = browser.new_context(storage_state="/Users/me/auth-profiles/booking.storage.json")

# After (post_process.portabilize_storage_path 적용 후)
import os
context = browser.new_context(storage_state=os.environ["AUTH_STORAGE_STATE_IN"])
```

재생 wrapper 가 `AUTH_STORAGE_STATE_IN` env 를 주입해서 *다른 머신/경로에서도 재생 가능*.

### 3.5 만료된 후 — 재시드 동선

다음 날 / 며칠 후 / 환경 변경 후 [▶ Start Recording] 클릭 시 verify 실패. **만료 모달** 자동 노출:

```
┌── ⚠ 인증 세션 만료 ────────────────────────────┐
│ 인증 세션 booking-via-naver 가 만료되었습니다.  │
│ 원인: 세션 만료 또는 IP 변경.                  │
│                                                │
│ 재시드하면 어제 입력한 시작 URL / 검증 URL /   │
│ 검증 텍스트(있을 때)가 그대로 유지됩니다.      │
│                                                │
│                  [취소]   [재시드]             │
└────────────────────────────────────────────────┘
```

**[재시드]** 클릭 → §3.2 의 시드 입력 모달이 *prefill 된 상태로* 다시 열림. prefill 우선순위:

1. 같은 브라우저 세션에서 *방금 직접 seed* 한 입력값 — 메모리에 보존된 마지막 폼.
2. 그 외 — `GET /auth/profiles/{name}` 으로 카탈로그에서 fetch:
   - `name` / `verify_service_url` / `verify_service_text`(있을 때) / `ttl_hint_hours` / `naver_probe` ✓ 자동 채움
   - `seed_url` 은 카탈로그에 저장 안 되므로 `verify_service_url` 의 origin 으로 *추정* (예: `https://booking.example.com/`). 다르면 사용자가 모달에서 수정.
3. 카탈로그 fetch 실패 시 `name` 만 채움.

같은 흐름으로 재시드 → 자동으로 녹화 재개.

**재생 만료시도 동일한 모달**: `▶ Play` (codegen / LLM) 클릭 시 만료가 감지되면 (HTTP 409 + `detail.reason=profile_expired`) 같은 만료 모달이 노출되며 [재시드] 버튼이 동일하게 동작 (post-review fix v3 — F3).

### 3.6 머신 불일치 경고

다른 머신에서 시드된 프로파일을 동기화한 뒤 사용 시:

```
┌── ⚠ 머신 불일치 경고 ──────────────────────────┐
│ 이 인증 세션은 다른 머신에서 시드되었습니다.   │
│ 네이버는 IP/디바이스 핑거프린트가 바뀌면       │
│ 세션을 자주 무효화합니다 — 거의 바로 재시드가  │
│ 필요해질 수 있습니다.                          │
│                                                │
│ 권장: 이 머신에서 새로 시드.                   │
│                                                │
│  [취소]   [그대로 시도]   [이 머신에서 새로 시드] │
└────────────────────────────────────────────────┘
```

차단은 안 함. 사용자 자각이 목표.

### 3.7 sessionStorage 경고 (D16)

⚠ **현재 동작 (v3 정정)**: sessionStorage detection 함수(`detect_session_storage_use`)는 구현되어 있으나, `playwright open` 의 close-time hook 부재로 *seed 파이프라인에서 자동 검출되지 않음*. 시드 결과의 `session_storage_warning` 은 항상 `False` 로 박힘.

UI 라벨 ⚠sessionStorage 가 노출되는 경로는 두 가지뿐:

1. 외부에서 `_index.json` 의 해당 프로파일을 직접 편집해 `session_storage_warning: true` 로 설정한 경우.
2. 후속 트랙의 *persistent userDataDir fallback* 이 도입된 후.

서비스가 sessionStorage 에 인증 토큰을 두면 storage_state 만으로 보존 안 되어 재시드 빈도가 올라갈 수 있음 (§6.3 의 한계 참조). 의심되는 서비스를 만나면 *수동으로 카탈로그 편집*해 라벨을 켜는 것이 현 시점 최선.

---

## 4. CLI 워크플로우 (대안)

UI 없이 동일 작업 가능 — 자동화 / 스크립트 통합용.

### 4.1 시드

```bash
python3 -m zero_touch_qa auth seed \
    --name booking-via-naver \
    --seed-url https://booking.example.com/ \
    --verify-service-url https://booking.example.com/mypage \
    --verify-service-text "김QA님 환영합니다" \
    --ttl-hint-hours 12
```

`playwright open` 창이 뜨고, 사용자가 직접 로그인 후 창을 닫으면 자동 등록.

옵션:
- `--no-naver-probe` — 네이버 측 weak probe 건너뜀 (service-only)
- `--service-domain DOMAIN` — seed-url 의 host 자동 추출 대신 명시
- `--notes "..."` — 자유 메모
- `--timeout-sec 600` — 사용자 입력 대기 한도

### 4.2 목록

```bash
python3 -m zero_touch_qa auth list

# JSON 출력 (스크립트 통합)
python3 -m zero_touch_qa auth list --json
```

출력 예:
```
# 1 profiles
NAME                           SERVICE                          LAST VERIFIED                 TTL
booking-via-naver              booking.example.com              2026-04-29T17:35:12+09:00      12h
```

### 4.3 명시적 verify

```bash
python3 -m zero_touch_qa auth verify --name booking-via-naver

# 만료 확인 (스크립트 게이트용)
python3 -m zero_touch_qa auth verify --name booking-via-naver --json
```

성공 종료코드 0 / 실패 1.

### 4.4 삭제

```bash
python3 -m zero_touch_qa auth delete --name booking-via-naver
```

카탈로그 + storage 파일 모두 정리.

---

## 5. E2E 테스트 실행 방법

### 5.1 슈트 구성

| Tier | 파일 | 포트 | 검증 |
|---|---|---|---|
| 1 | `test/e2e_p1_auth_profiles.py` | (없음) | auth_profile 풀 사이클 (시뮬 시드 + verify + 만료) |
| 2 | `test/test_auth_profile_api_e2e.py` | 18094 | HTTP API 5개 + recording_start 통합 |
| 3 | `test/test_auth_profile_ui_e2e.py` | 18095 | 인증 블록 / 4 모달 / 만료-재시드 동선 |
| (기존) | `test/test_recording_ui_e2e.py` | 18093 | Recording UI 회귀 |

### 5.2 단독 실행

#### Tier 1 (standalone Python)

```bash
cd playwright-allinone
PYTHONPATH=. ~/.dscore.ttc.playwright-agent/venv/bin/python3 \
    test/e2e_p1_auth_profiles.py
```

stdout 으로 단계별 결과. 헤드드 Chromium 창이 잠깐 뜸 (~3s).

#### Tier 2 (pytest, API e2e)

```bash
cd playwright-allinone
PYTHONPATH=. ~/.dscore.ttc.playwright-agent/venv/bin/python3 \
    -m pytest test/test_auth_profile_api_e2e.py -v
```

격리된 데몬 spawn → HTTP 호출 → cleanup.

#### Tier 3 (pytest, UI e2e)

```bash
cd playwright-allinone
PYTHONPATH=. ~/.dscore.ttc.playwright-agent/venv/bin/python3 \
    -m pytest test/test_auth_profile_ui_e2e.py -v
```

헤드리스 Chromium 자동 진입 → DOM 검증.

### 5.3 일괄 실행 (3 슈트 + 기존 회귀)

```bash
cd playwright-allinone
PYTHONPATH=. AUTH_PROFILE_VERIFY_HEADLESS=1 \
    ~/.dscore.ttc.playwright-agent/venv/bin/python3 -m pytest \
    test/test_recording_ui_e2e.py \
    test/test_auth_profile_api_e2e.py \
    test/test_auth_profile_ui_e2e.py \
    -v --tb=short
```

`AUTH_PROFILE_VERIFY_HEADLESS=1` — 운영 기본은 headed (D9), 테스트는 headless.
운영 headed 검증은 사용자가 검증 대상 페이지를 눈으로 확인할 수 있게 기본 `slow_mo=500ms`,
도착 후 `hold=4000ms` 를 적용한다. 필요 시 `AUTH_PROFILE_VERIFY_SLOW_MO_MS`,
`AUTH_PROFILE_VERIFY_HOLD_MS` 로 조정한다.

소요: ~30~40s (변경 영역 대비 환경 차이 있음).

### 5.4 pre-commit hook 자동 실행

설치 (1회):

```bash
playwright-allinone/scripts/install-git-hooks.sh
```

이후 `git commit` 시 변경 영역이 매치되면 e2e 슈트 자동 실행. 회귀 시 commit 차단.

**트리거 영역**:
- `recording_service/*`
- `zero_touch_qa/{auth_profiles,executor,__main__}.py`
- `test/test_recording_(ui_e2e|service).py`
- `test/test_auth_profile_*.py`
- `test/test_(post_process|replay_proxy_auth|executor_fingerprint_env|auth_profiles|auth_cli)*.py`

**우회**: `git commit --no-verify` (긴급시만).

**자동 skip 조건**:
- venv python 미존재
- port 18093 / 18094 / 18095 점유 중

### 5.5 단위 / 통합 테스트 (e2e 외)

```bash
# auth-profile 관련 단위 + 통합 (334 케이스, ~2.5s)
cd playwright-allinone
~/.dscore.ttc.playwright-agent/venv/bin/python3 -m pytest \
    test/test_auth_profiles.py \
    test/test_auth_cli.py \
    test/test_post_process.py \
    test/test_replay_proxy_auth.py \
    test/test_executor_fingerprint_env.py \
    test/test_recording_service.py \
    -v
```

---

## 6. 트러블슈팅

### 6.1 시드 단계에서 자주 만나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| 별도 창이 안 뜸 | `playwright` CLI 가 PATH 에 없음 | `playwright install chromium` |
| 시드 timeout (창을 안 닫았는데 종료) | `--timeout-sec` 한도 초과 (기본 600s) | `--timeout-sec 1800` 으로 증가 |
| 시드 후 "dump 가 비어있음" | 사용자가 로그인 미완료 상태로 창 닫음 | 다시 시드 — 본인 이름 확인 후 닫기 |
| 시드 후 "naver.com 쿠키 누락" | 사용자가 다른 OAuth (구글/카카오) 클릭했거나 OAuth 미완료 | "네이버로 로그인" 버튼 정확히 클릭 |
| 시드 후 verify 실패 | 검증 URL/텍스트 부정확 | 검증 URL 직접 브라우저로 열어 본인 이름이 어떤 정확한 문구로 보이는지 확인 |
| `ChipsNotSupportedError` | Playwright <1.54 | `pip install --upgrade playwright` (≥1.54) |

### 6.2 녹화 / 재생에서 자주 만나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| 녹화 시작 시 만료 모달 | 시드된 세션의 storage / 네이버 세션 만료 | 모달의 [재시드] |
| 녹화 시작 시 머신 불일치 모달 | 시드한 머신과 다른 머신 | 이 머신에서 새 시드 권장 |
| 재생 시 ReplayAuthExpiredError | 재생 시점에 verify 실패 | UI 가 자동으로 만료 모달 노출 — [재시드] |
| `original.py` 실행이 절대 경로로 깨짐 | 다른 머신에서 실행 (portabilize 누락) | 녹화 직후 자동으로 portabilize 됨. 누락 시 `recording_service.post_process.portabilize_storage_path()` 수동 실행 |

### 6.3 본질적 한계 (수용 필요)

1. **재시드는 "일 1회 floor" 가정.** 며칠~주 단위는 운에 가깝다.
2. **시드 머신 = 재생 머신.** 다른 머신으로 옮기면 사실상 매번 재시드.
3. **Jenkins / 공유 CI 안정 회귀로는 부적합.** 그게 목표면 callback mock 트랙이 필요 (현 scope 외).
4. **개인 네이버 계정으로 시드하면 막힐 가능성↑.** 시드 전용 신규 테스트 계정 권장.
5. **서비스가 OAuth `auth_type=reauthenticate` 또는 step-up auth 를 강제하는 민감 페이지** (결제·탈퇴·개인정보 변경 등) 는 storage 재사용으로 우회 안 됨. 시나리오 분할 권장.
6. **서비스가 인증 토큰을 sessionStorage 에 두면** storage_state 단독으로 보존 안 됨. 시드 시 detection 으로 노란 경고 라벨 노출. fallback 트랙은 후속 (persistent userDataDir).

### 6.4 디버깅 명령

```bash
# 카탈로그 상태 확인
python3 -m zero_touch_qa auth list

# 특정 프로파일 verify 결과 + 사유
python3 -m zero_touch_qa auth verify --name <name> --json

# storage dump 의 도메인별 쿠키 개수 직접 확인
jq '[.cookies[] | .domain] | group_by(.) | map([.[0], length])' \
    ~/ttc-allinone-data/auth-profiles/<name>.storage.json

# Partitioned/CHIPS 쿠키 존재 여부
jq '[.cookies[] | select(.partitionKey != null)] | length' \
    ~/ttc-allinone-data/auth-profiles/<name>.storage.json

# Recording 서비스 live stdout/stderr + auth seed phase 확인
tail -f ~/.dscore.ttc.playwright-agent/recording-service.log
```

시드 중에는 로그에 다음 phase 가 남는다.

| phase | 의미 |
|---|---|
| `login_waiting` | 별도 브라우저에서 사용자가 로그인/2중 확인을 수행 중 |
| `verifying` | 창이 닫혀 storage 저장 완료, 검증 대상 페이지를 headed Chromium 으로 확인 중 |
| `ready` | 프로파일 저장 완료. UI 에서 `사용하지 않음` / `이 프로파일 사용` 선택 대기 |
| `error` | dump/verify/입력 오류로 시드 실패. UI 의 `다시 입력` 으로 재시도 |

---

## 7. 보안 체크리스트

- [x] storage 파일 권한 0600 / 디렉토리 0700 (자동 강제됨)
- [x] `auth-profiles/` 가 `.gitignore` 에 포함 — root `.gitignore` 에 `auth-profiles/` + `*.storage.json` 추가됨 (v3 F1)
- [ ] 백업 / artifact / CI cache 에 `auth-profiles/` 포함 안 됨
- [ ] 운영 계정 사용 안 함 (테스트 전용 계정)
- [ ] CLI 첫 사용 시 1회 운영 계정 금지 안내 확인
- [ ] storage 파일이 포함된 디스크 dump 를 외부 공유하지 않음
- [ ] 로그/스크린샷에 storage 경로/쿠키값 노출 없음 (마스킹 자동 적용)

---

## 8. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-04-29 | 초안 — P1~P5 전체 + 3-tier e2e 마감 + pre-commit hook 등록 후 작성 |
| 2026-04-29 | post-review fix v3 — (F1) `.gitignore` 에 `auth-profiles/` + `*.storage.json` 추가. (F2) `recording_start` 의 auth 검증을 `registry.create` 전으로 이동 (orphan pending 세션 차단) + 회귀 테스트. (F3) `rplus/router.py` 의 `play-codegen`/`play-llm` 이 `ReplayAuthExpiredError` 를 502 가 아니라 409 + `detail.reason="profile_expired"` 로 반환, UI 의 `_runPlay` 가 받아 만료 모달로 분기 + 회귀 테스트. (F4) `recording_stop` 의 metadata 갱신이 `auth_profile` 키를 잃던 버그 수정 (`_save_metadata_preserving_auth` 헬퍼). (F5) 만료 모달 [재시드] 가 카탈로그 detail 까지 fetch 해 `verify_service_url`/`text`/`ttl`/`probe` 까지 prefill — `GET /auth/profiles/{name}` 신설. §3.5 / §3.7 / §1.2 D16 본문 정정 |
| 2026-04-29 | seed UX 현행화 — 검증 텍스트 선택화, 텍스트가 없으면 검증 URL 접근 약검증. 시드 진행 상태를 `login_waiting`/`verifying`/`ready`/`error` 로 노출하고, headed verify 는 slow_mo+hold 로 사용자가 검증 대상 페이지를 확인할 수 있게 함. 성공 시 `사용하지 않음`/`이 프로파일 사용` 선택을 분리하고, 실패 시 `다시 입력` 으로 prefill 재시도. `recording-service.log` 에 stdout/stderr 와 seed phase 기록 |

## 9. 관련 문서

- 설계 문서: [PLAN_AUTH_PROFILE_NAVER_OAUTH.md](PLAN_AUTH_PROFILE_NAVER_OAUTH.md)
- 기존 인증(`auth_login` DSL) 가이드: [auth-login-usage.md](auth-login-usage.md)
- Recording 서비스 설계: [PLAN_GROUNDING_RECORDING_AGENT.md](PLAN_GROUNDING_RECORDING_AGENT.md)
- 운영 readiness: [PLAN_PRODUCTION_READINESS.md](PLAN_PRODUCTION_READINESS.md)
