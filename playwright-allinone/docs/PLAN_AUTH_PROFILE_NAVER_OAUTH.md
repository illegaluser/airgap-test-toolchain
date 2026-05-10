# PLAN — Auth Profile (Naver-OAuth 연동 서비스 E2E 테스트)

> **Status**: v3 (post-review fix 반영, 2026-04-29) · P1~P5 + 3-tier e2e 마감
> **Scope**: Recording UI / `zero_touch_qa` 에 인증 프로파일(auth-profile) 도입.
> 네이버로 로그인되는 *제3자 서비스* 의 E2E 녹화/재생 시 2중 확인 화면이
> 테스트 경로에 들어오지 않도록 한다.
> **History**: v1 (초안) → v2 (외부 리뷰 반영) → v3 (post-review fix — §15 참조)

---

## 0. Executive Summary

### 0.1 한 줄 요약

> 사람이 한 번 직접 통과시킨 네이버 로그인 결과(storageState)를 저장해두고,
> 이후 녹화·재생에서는 그 세션을 자동 재주입해서 *대상 서비스* 의 기능 테스트만 수행한다.
> 시드는 1회, 재시드는 일 1회 floor 가정.

### 0.2 사용자 부담

| 행동 | 빈도 |
|---|---|
| 네이버 ID/PW + 2중 확인 직접 통과 | **시드 1회 + 만료 시마다 재시드 (≈ 일 1회 floor)** |
| 일상 녹화 / 재생 (대상 서비스 기능) | n회 — *대부분의 경우* 네이버 화면 등장 0번 |

> **"네이버 화면 0번" 보장이 아님.** 만료·환경 변경·서비스 측 reauth 강제·sessionStorage 의존 등으로 네이버 왕복이 다시 트리거될 수 있다. 이런 경우는 빠르게 감지해서 재시드로 유도하는 것이 본 시스템의 역할.

### 0.3 적용 범위와 비-적용 범위

| 사용 가능 | 사용 불가 |
|---|---|
| 개인 QA 로컬 회귀 | 팀 공유 Jenkins/CI 안정 회귀 |
| 단일 머신 + 안정 IP | 회전 IP / 공용 runner pool |
| 데모/시연 직전 검증 | "주 단위 무중단" 자동화 |
| 며칠 주기 수동 재시드 가능 환경 | 무인 24/7 회귀 |

> **CI 안정 회귀가 진짜 목표**라면 이 설계로는 못 간다 — 별 트랙으로 callback
> mock 또는 테스트 세션 API 가 필요. (현 PR scope 외)

---

## 1. 배경

### 1.1 문제

테스트 대상 서비스(예: 사내 예약 시스템 `booking.example.com`)가 "네이버로
로그인" OAuth 를 인증 옵션 중 하나로 제공한다. 이 서비스의 E2E 회귀를 돌리려면
로그인 상태가 필요한데:

1. 네이버는 *새로운 환경에서 로그인* 시 2중 확인(SMS / 디바이스 등록 등)을
   강제한다.
2. 네이버는 우리 통제 밖이다 — 협조 요청 불가, 정책 변경 불가, mock 생성 불가.
3. 일반적 자동화로 2중 확인을 돌파하려는 시도는 봇 탐지·CAPTCHA 빈도↑ 로
   오히려 불안정해진다.

### 1.2 검토한 대안 4가지와 선택

| 대안 | 채택 여부 | 이유 |
|---|---|---|
| 네이버 2중 확인 자동 통과 | **❌** | 보안 절차 우회. CAPTCHA·정책 변경에 깨지기 쉬움 |
| **수동 로그인 1회 + storageState 재사용** | **✅** | 빠른 적용. 네이버 측 협조 불필요 |
| 테스트 전용 세션 생성 API | ❌ (현 scope 외) | 서비스 백엔드 협조 필요 |
| OAuth callback mock | ❌ (현 scope 외) | 서비스 백엔드 협조 필요 |

> **선택 이유**: 사용자가 명시 — *"지금은 1번밖에 구현할 수 없어. 네이버측과
> 어떠한 접점도 없기 때문에 무언가를 요구할 수 없거든."* (2026-04-29)

### 1.3 핵심 멘탈 모델 — 네이버는 통과점, 진짜 대상은 연동 서비스

#### 헷갈리기 쉬운 그림 (✗)
```
대상 = 네이버
[녹화] → [네이버 로그인 화면 자동화 시도]
```

#### 실제 그림 (✓)
```
대상 = booking.example.com (네이버로 로그인 지원)

[녹화] → [booking.example.com 의 회의실 예약 화면]
         ↑
         "이미 로그인된 상태" 가 필요
         ↑
[시드 1회] booking.example.com → "네이버로 로그인" 버튼
            → nid.naver.com (사람이 ID/PW + 2중 확인 통과)
            → booking.example.com 로 redirect
            → booking 이 자체 세션 쿠키 발급
            → 사람이 창 닫음
            → storage 저장 (.naver.com 쿠키 + .booking.example.com 쿠키 둘 다)
```

→ storage 에는 **두 도메인의 쿠키가 함께** 저장된다. 그래서 한 번 시드하면
이후 녹화는 booking.example.com 만 열어도 이미 로그인된 상태가 된다.

이 한 줄이 설계의 척추.

---

## 2. 의사결정 로그

| # | 결정 | 일자 | 근거 |
|---|---|---|---|
| D1 | 방식 = storageState 재사용만 (callback mock / 테스트 세션 API 제외) | 2026-04-29 | 네이버 협조 불가 + 서비스 백엔드 변경 가능 여부 미확정 |
| D2 | 시드 도구 = `playwright open --save-storage` | 2026-04-29 | codegen 의 inspector 창 노출 없이 깔끔. 사용자 혼란 적음 |
| D3 | `original.py` 의 storage 경로 = env var 치환 | 2026-04-29 | 다른 환경 이식성 (절대 경로 박힘 방지) |
| D4 | 시드 파일 위치 = `~/ttc-allinone-data/auth-profiles/` | 2026-04-29 | 기존 ttc-allinone-data 데이터 디렉토리에 흡수 |
| D5 | 자동 verify = Start 직전 항상 실행 | 2026-04-29 | 만료 자각 빠름, 실패 비용 작음 (HEAD/GET 1회) |
| D6 | **네이버는 통과점 — 대상은 연동 서비스** (멘탈 모델 정정) | 2026-04-29 | 사용자 명시 |
| D7 | 목표 사용자 = 개인 QA, 단일 머신, 며칠 주기 회귀 | 2026-04-29 | 외부 feasibility audit 결과 — CI 안정 회귀는 다른 트랙 |
| D8 | 재시드 cadence 기대치 = "일 1회 floor" 로 명시 | 2026-04-29 | audit: storage TTL 며칠 가정은 운에 가까움. 사용자 기대치 정렬이 더 중요 |
| D9 | 재생도 headed 강제 (headless 옵션 X, 첫 릴리스) | 2026-04-29 | audit: navigator.webdriver / fingerprint mismatch → 재인증 강제 가능성 높음 |
| **D10** | **fingerprint pin 대상 = viewport / locale / timezone_id / color_scheme / Playwright 버전 + 채널 + headed mode. UA 는 capture-only (임의 spoof 금지)** | 2026-04-29 (v2 정정) | UA 만 spoof 하면 sec-ch-ua Client Hints 와 어긋나 *오히려* 봇 의심. 같은 Playwright 버전·채널 사용 시 UA 자연 일치 |
| D11 | 시드 머신 ≠ 재생 머신 검출 시 빨간 경고 (차단까지는 아님) | 2026-04-29 | audit: 머신/IP 결속. 다른 머신 사용 시 사용자가 자각해야 함 |
| D12 | dump 검증 = 시드 직후 양 도메인 쿠키 존재 강제 | 2026-04-29 | audit: GH #15481/#29212 회귀 보호 |
| **D13** | **verify = service-side authoritative + naver-side optional weak probe.** 가공 silent-refresh 엔드포인트 사용 안 함 | 2026-04-29 (v2 정정) | 네이버에 우리가 호출 가능한 공개 cookie-verify endpoint 없음. naver_probe 는 `https://nid.naver.com/` 이동 후 *로그인 폼 미노출* 같은 약한 negative check 만 |
| **D14** | **CHIPS 가드 = Playwright 버전 게이트.** `playwright>=1.54` 면 Partitioned 쿠키 정상 보존 → pass-through. 미만이면 시드 단계 거절 | 2026-04-29 (v2 정정) | `partition_key` 는 Playwright 1.54 도입. 로컬은 1.57. 즉 *현 시점 이미 지원됨* |
| **D15** | **scenario.json 형식 변경 금지.** auth_profile 메타는 세션 디렉토리의 `metadata.json` 에 저장 (별도 envelope 도입 안 함) | 2026-04-29 (v2 신설) | `zero_touch_qa.__main__:410` 가 `isinstance(scenario, list)` 강제. `_meta` 박으면 즉시 ScenarioValidationError. 영향 폭 최소화 |
| **D16** | **sessionStorage 의존 서비스는 detection 함수만 구현, seed 통합은 후속 트랙.** ``detect_session_storage_use()`` 는 외부 입력 dict 를 분석할 수 있지만, ``playwright open`` 이 sessionStorage 캡처 hook 을 노출하지 않아 seed 파이프라인에서는 항상 ``session_storage_warning=False`` 로 박힘 (P1 한계 명시). UI 라벨 ⚠sessionStorage 는 *외부에서 카탈로그를 직접 편집*해 True 로 박은 경우만 노출 — Tier 3 e2e 의 ``ui-with-ss`` 픽스처가 이 경로 검증 | 2026-04-29 (v3 정정) | playwright open 의 close-time hook 부재. fallback 은 §12 의 persistent userDataDir |
| **D17** | **번들 zip 흐름 폐기 → `.py` 일원화.** Recording UI 의 `📦 모니터링 번들 다운로드` 모달 / `bundle-modal` / `/recording/sessions/{sid}/bundle` 엔드포인트 / `auth_flow.pack_bundle` / `recording_tools.py` CLI / Replay UI 의 `/api/bundles` 흐름 / `orchestrator.run_bundle` 모두 제거. 대신 Recording UI 의 `⬇ 다운로드` 가 sanitize 통과 `.py` 만 내려주고, Replay UI 가 `.py` 를 받아 사용자가 (1) 적용할 프로파일을 select 또는 *비로그인* 명시 (2) verify URL 을 카탈로그 fallback 또는 사용자 입력으로 결정해 실행. 자기-기술 metadata (alias, verify_url, README, script_provenance) 는 모두 *받는 쪽 UI 입력 + 카탈로그* 로 흡수 | 2026-05-11 (사용자 결정) | 사용자 명시: "번들이 왜 필요하지? 그냥 스크립트만 실행하면 되는거다. 해당 스크립트가 로그인 프로파일이 적용/미적용될 경우만 감안하면 되는거다." 분석 결과 (1) 번들 강제 alias 가 *비로그인 녹화는 지원하면서 비로그인 번들/재생은 거부* 하는 정합 불일치를 만들었고 (2) 자기-기술 packaging 의 가치는 *무인 자동화/CI* 흐름에만 의미 있는데 본 시스템은 사람-운영 (D7) scope 라 잉여 — 결과적으로 사용자 경험 저해. .py 일원화로 *비로그인 시나리오* 자동 지원 + UX 단일화 |

---

## 3. 사용자 이용 플로우 (Recording UI 위)

가공 예시 — **회사 사내 예약 시스템 `booking.example.com` (네이버로 로그인 지원)**.
테스터: **김QA**, 처음 사용.
테스트 목적: "네이버 계정 사용자가 회의실 예약을 정상 생성할 수 있는가?"

### 3.1 DAY 1 — 시드 (시드 1회)

#### Step 1. UI 진입

```
[Recording UI 메인 — 새 녹화 시작 카드]

target_url       : [_______________________]
planning_doc_ref : [_______________________]

인증 (선택)
  Auth Profile  : [(없음 — 비로그인 녹화) ▼]
                  [+ 새 세션 시드]

[▶ Start Recording]   [📁 Play Script from File]
```

드롭다운에 "(없음)" 만 있다 — 시드된 게 아무것도 없다.

#### Step 2. "+ 새 세션 시드" 클릭 → 모달

```
┌── 새 인증 세션 만들기 ─────────────────────────────────────┐
│                                                            │
│ 이 단계는 1회만 수행합니다 (만료 시 재시드).               │
│ 사람이 직접 네이버에 로그인 + 2중 확인을 통과하면, 그      │
│ 결과 세션을 저장해서 이후 녹화/재생에 자동으로 재사용.     │
│                                                            │
│ 이름        : [booking-via-naver___________]               │
│   ↳ 이 프로파일 식별 이름 (영문/숫자/_/-)                  │
│                                                            │
│ 시작 URL    : [https://booking.example.com/_______]        │
│   ↳ ⚠️ 테스트 대상 서비스의 진입 페이지                    │
│      (네이버 로그인 페이지가 아닙니다!)                    │
│                                                            │
│ 검증 URL    : [https://booking.example.com/mypage_]        │
│   ↳ 로그인된 사용자만 볼 수 있는 페이지                    │
│                                                            │
│ 검증 텍스트 : [김QA님 환영합니다__________________] (선택) │
│   ↳ 있으면 로그인 상태 문구까지 확인, 비우면 URL 접근 확인 │
│                                                            │
│ TTL 힌트    : [12] 시간   ⓘ 일 1회 재시드 가정 권장        │
│                                                            │
│ ⚠ 운영 계정 사용 금지 — 테스트 전용 계정 권장              │
│                                                            │
│                            [열기 →]   [취소]               │
└────────────────────────────────────────────────────────────┘
```

[열기 →] 클릭.

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
[취소]
```

별도 Chromium 창이 뜸 — `booking.example.com` 첫 화면.

#### Step 4. 별도 창에서 김QA 가 직접 수행

```
[별도 창 — booking.example.com]
  로그인 방법:
  [구글로 로그인] [카카오로 로그인] [네이버로 로그인]   ← 클릭
                                       ↓
[별도 창 — nid.naver.com]
  ID:  tester01      PW:  ********    [로그인]            ← 직접 입력
                                       ↓
[별도 창 — 네이버 2중 확인]
  SMS 인증번호 [____]                  [확인]              ← SMS 받아 입력
                                       ↓
[별도 창 — booking.example.com/auth/naver/callback?...]
  → 자동 redirect →
[별도 창 — booking.example.com 메인]
  김QA님 환영합니다 ✓                                    ← 확인 후 창 X 클릭
```

#### Step 5. 백엔드 자동 처리

창 닫히는 순간:
```
1. Playwright 가 storage 자동 dump
   → ~/ttc-allinone-data/auth-profiles/booking-via-naver.storage.json
   - cookies + localStorage + IndexedDB (Playwright >=1.51, indexed_db=True)
   - sessionStorage 는 보존 안 됨 (Playwright 한계 — D16)
   - mode 0600

2. dump 검증 (D12)
   - .naver.com 도메인 쿠키 ≥1 개 ?    ✓  (endsWith(".naver.com") or == "naver.com")
   - .booking.example.com 도메인 쿠키 ≥1 개 ? ✓ (서비스 도메인 endsWith 매칭)
   - 둘 중 하나라도 없으면 → 시드 실패 + storage 파일 삭제

3. sessionStorage detection (D16)
   - 시드 시 last page 의 sessionStorage.length 캡처
   - >0 이면서 'token'/'auth'/'session' 같은 의심 키 발견 시
     → 등록은 진행하되 노란 경고 라벨 추가
       ("이 서비스는 sessionStorage 에 인증 데이터가 있을 수 있습니다.
         재시드 빈도 ↑ 가능 — userDataDir fallback 트랙 §12 참조.")

4. service-side verify (D13 — authoritative)
   - 새 headed Chromium 컨텍스트 (D9) + fingerprint pinning (D10)
   - storage 적용
   - https://booking.example.com/mypage 이동 → "김QA님 환영합니다" 검색 → ✓
   - headed 모드에서는 slow_mo + hold 로 검증 대상 페이지를 잠시 보여준 뒤 자동 종료
   - 기본값: `AUTH_PROFILE_VERIFY_SLOW_MO_MS=500`, `AUTH_PROFILE_VERIFY_HOLD_MS=4000`

5. naver-side optional weak probe (D13 — best-effort)
   - https://nid.naver.com/ 이동
   - 로그인 폼 (input[name="id"]) 노출 여부 negative check
     (= 노출되면 "로그인 안 된 상태", 안 보이면 "로그인된 것으로 추정")
   - 실패해도 시드 통과 (warn-only)

6. 카탈로그 등록
   → ~/ttc-allinone-data/auth-profiles/_index.json
   - last_verified_at = 방금
   - fingerprint = 시드 시 사용한 viewport/locale/timezone_id/color_scheme +
                   captured UA + Playwright 버전 + 채널 (D10)
   - host_machine_id = hostname + 디스크 UUID 해시 (D11)
   - chips_supported = (Playwright 버전 ≥1.54 — D14)
```

UI 화면:
```
✅ 시드 완료
   프로파일      : booking-via-naver
   service verify : 통과 (방금)
   naver probe    : 통과 (best-effort)
   유효 시간(추정): 12시간
   storage 파일  : ~/ttc-allinone-data/auth-profiles/booking-via-naver.storage.json (0600)

   이번 녹화에 사용할지 선택하세요. 사용하지 않아도 프로파일은 목록에 저장됩니다.
                                      [사용하지 않음] [이 프로파일 사용]
```

`이 프로파일 사용` 은 메인 폼의 Auth Profile 선택값을 새 프로파일로 바꾼다.
`사용하지 않음` 은 저장은 유지하되 이번 녹화의 선택값은 비워 둔다.

#### Step 6. 진짜 녹화 시작

- target_url: `https://booking.example.com/booking/new`
- Auth Profile: `booking-via-naver`
- [▶ Start Recording] 클릭

백엔드:
```
1. 자동 verify (Start 직전, D5) — service-side 필수, naver probe 약식
2. host_machine_id 일치 ?  → 불일치 시 빨간 경고 모달 (D11)
3. playwright codegen \
       https://booking.example.com/booking/new \
       --target=python \
       --output=<session>/original.py \
       --load-storage=<storage_path> \
       --viewport-size 1280,800 \
       --lang ko-KR \
       --timezone Asia/Seoul \
       --color-scheme light
   (UA 옵션 없음 — D10. 같은 Playwright 1.57 + chromium channel 사용으로 자연 일치)
```

별도 codegen 창이 *이미 로그인된 회의실 예약 페이지*에서 직접 시작.
**대부분의 경우** 네이버 화면은 안 나옴.

평소처럼 액션 수행 → [■ Stop & Convert].

#### Step 7. 결과

```
[결과]
  세션 ID         : abc123def456
  state           : ✓ done
  step 수         : 6
  scenario.json   : .../sessions/abc123def456/scenario.json
  metadata.json   : .../sessions/abc123def456/metadata.json
  인증 프로파일   : booking-via-naver

[Scenario JSON]   ← list[dict] 그대로, 변경 없음
[
  {"step": 1, "action": "navigate", "value": "https://booking.example.com/booking/new"},
  {"step": 2, "action": "click",    "target": "role=combobox, name=회의실"},
  ...
]

[metadata.json]   ← 여기에 auth_profile 박힘
{
  "id": "abc123def456",
  "target_url": "https://booking.example.com/booking/new",
  "auth_profile": "booking-via-naver",
  "fingerprint_snapshot": { ... },
  "created_at": "..."
}
```

> **D15 적용**: scenario.json 은 list 형식 유지. auth_profile 메타는 세션 디렉토리의 `metadata.json` 에 박힘. `zero_touch_qa.executor` / `report.save_scenario` 의 list 강제 시그니처를 깨지 않음.

#### Step 8. 재생 (▶ LLM 적용 코드 실행)

```
1. session metadata.json 에서 auth_profile = "booking-via-naver" 읽음
2. service-side verify (필수) → 통과
3. python3 -m zero_touch_qa --mode execute \
       --scenario .../scenario.json \
       --storage-state-in <storage_path>
   + env: PLAYWRIGHT_VIEWPORT=1280x800
          PLAYWRIGHT_LOCALE=ko-KR
          PLAYWRIGHT_TIMEZONE=Asia/Seoul
          PLAYWRIGHT_COLOR_SCHEME=light
4. 재생 창이 *이미 로그인된 예약 페이지*에서 시작 → 6 step 모두 PASS
```

### 3.2 DAY 2 — 같은 머신, 다른 시나리오 추가

```
[Recording UI 메인]

target_url    : [https://booking.example.com/approval/list]
Auth Profile  : [booking-via-naver ▼]   ← 어제 시드한 게 그대로 있음
                ⏱ 어제 17:30 검증 — Start 시점에 자동 재검증 예정
```

[▶ Start Recording] 클릭 → 자동 verify 통과 → 결재 페이지 codegen.

→ **김QA 는 어제 이후로 네이버 로그인 화면을 다시 보지 않음** (만료 전까지).

### 3.3 DAY 8 — 만료된 후 (일 1회 floor 가정)

평소처럼 [▶ Start Recording] 클릭. 자동 verify 가 실패 (네이버 세션 만료 또는
IP 변경 또는 서비스 자체 세션 만료).

```
⚠ 인증 세션 'booking-via-naver' 가 만료되었습니다.

원인 (추정): 세션 만료 또는 IP 변경.
재시드하면 어제 입력값(시작 URL / 검증 URL / 검증 텍스트가 있을 때)이
그대로 유지됩니다.

                          [재시드]   [취소]
```

[재시드] 클릭 → Step 3~5 그대로 다시 진행 → 자동으로 녹화 재개.

### 3.4 다른 머신에서 옛 세션 가져왔을 때

다른 노트북에서 `~/ttc-allinone-data/auth-profiles/` 를 동기화한 뒤 같은
세션 사용 시도:

```
⚠ 머신 불일치 경고

이 인증 세션은 다른 머신에서 시드되었습니다 (시드 머신: ALPHA-MAC,
현재 머신: BETA-MAC). 네이버는 IP/디바이스 핑거프린트가 바뀌면
세션을 자주 무효화합니다 — 거의 바로 재시드가 필요해질 수 있습니다.

권장: 이 머신에서 새로 시드.

         [그래도 시도]   [이 머신에서 새로 시드]   [취소]
```

차단까지는 아님. 사용자 자각이 목표.

### 3.5 reauth 강제 / sessionStorage 의존 — 우회 불가 케이스

서비스가 결제·탈퇴·개인정보 변경 같은 민감 페이지에서 OAuth `auth_type=reauthenticate` 또는 자체 step-up auth 를 강제하면, storage 재사용으로도 우회되지 않는다 (§11 한계). 시나리오 도중 네이버 로그인 화면이 떠 step 이 FAIL 하면 사용자가 *해당 step* 만 수동 처리하거나, 시나리오를 *민감 액션 직전* 까지로 자르는 것이 권장 운영.

마찬가지로 서비스가 sessionStorage 에 인증 토큰을 두면 storage_state 단독으로는 보존 안 됨 (§11). 시드 단계의 detection (Step 5 #3) 이 사용자에게 미리 경고하므로, 그 경우 §12 의 userDataDir fallback 트랙으로 전환 검토.

---

## 4. UI 변경 명세

### 4.1 파일별 변경 요약

| 파일 | 변경 |
|---|---|
| `recording_service/web/index.html` | "새 녹화 시작" 카드에 "인증 (선택)" 블록 + 시드 모달 + 만료 모달 + 머신 불일치 모달 마크업 추가 |
| `recording_service/web/app.js` | 드롭다운 채우기, 시드 시작/폴링, verify, 재시드 모달, 머신 일치 검사 |
| `recording_service/web/style.css` | 인증 블록 + 모달 스타일 (기존 `.card` 패턴 재사용) |

### 4.2 수정되는 카드 1개 — "새 녹화 시작"

```diff
  <section class="card">
    <h2>새 녹화 시작</h2>
    <form id="start-form">
      <label> target_url ... </label>
      <label> planning_doc_ref ... </label>
+     <fieldset class="auth-block">
+       <legend>인증 (선택)</legend>
+       <label>
+         <span>Auth Profile</span>
+         <select name="auth_profile" id="auth-profile-select">
+           <option value="">(없음 — 비로그인 녹화)</option>
+         </select>
+         <button type="button" id="btn-auth-verify">↻ verify</button>
+       </label>
+       <p class="auth-status" id="auth-status">—</p>
+       <button type="button" id="btn-auth-seed">+ 새 세션 시드</button>
+     </fieldset>
      <div class="form-actions">
        <button type="submit" id="btn-start">▶ Start Recording</button>
        ...
      </div>
    </form>
  </section>
```

### 4.3 신규 모달 4개

1. **시드 입력 모달** (`#auth-seed-dialog`) — name / seed_url / verify_url / verify_text / TTL hint 입력
2. **시드 진행 모달** (`#auth-seed-progress`) — 별도 창이 떴으니 직접 통과하라는 안내 + polling 상태
3. **만료 알림 모달** (`#auth-expired-dialog`) — Start 자동 verify 실패 시 표시
4. **머신 불일치 모달** (`#auth-machine-mismatch-dialog`) — host_machine_id 불일치 시 경고

### 4.4 결과 카드 메타 추가

`<dt>인증 프로파일</dt><dd id="result-auth-profile">—</dd>` 한 줄.

### 4.5 세션 목록 컬럼 추가

`<th>auth</th>` + 행마다 `<td>{auth_profile|—}</td>`.

---

## 5. 백엔드 구현 계획

### 5.1 디렉토리 / 파일 레이아웃

```
~/ttc-allinone-data/auth-profiles/        (mode 0700)
├── _index.json                           # 카탈로그 (mode 0600)
├── booking-via-naver.storage.json        # storageState (mode 0600)
└── ...
```

`.gitignore` 갱신:
```
auth-profiles/
*.storage.json
```

### 5.2 `_index.json` 스키마 (v2)

```json
{
  "version": 1,
  "profiles": [
    {
      "name": "booking-via-naver",
      "service_domain": "booking.example.com",
      "storage_path": "booking-via-naver.storage.json",
      "created_at": "2026-04-29T17:30:00+09:00",
      "last_verified_at": "2026-04-29T17:35:12+09:00",
      "ttl_hint_hours": 12,
      "verify": {
        "service_url": "https://booking.example.com/mypage",
        "service_text": "김QA님 환영합니다",
        "naver_probe": {
          "url": "https://nid.naver.com/",
          "kind": "login_form_negative",
          "selector": "input[name='id']"
        }
      },
      "fingerprint": {
        "viewport": {"width": 1280, "height": 800},
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "color_scheme": "light",
        "playwright_version": "1.57.0",
        "playwright_channel": "chromium",
        "captured_user_agent": "Mozilla/5.0 ..."
      },
      "host_machine_id": "ALPHA-MAC:abcd1234",
      "chips_supported": true,
      "session_storage_warning": false,
      "verify_history": [
        {"at": "2026-04-29T17:35:12+09:00", "ok": true, "service_ms": 230, "naver_probe_ms": 180}
      ],
      "notes": ""
    }
  ]
}
```

### 5.3 신규 파일

```
zero_touch_qa/auth_profiles.py             ~320 LOC
test/test_auth_profiles.py                 ~300 LOC
docs/auth-profile-usage.md                 ~250 LOC
```

### 5.4 변경 파일

| 파일 | LOC | 역할 |
|---|---:|---|
| `zero_touch_qa/__main__.py` | ~60 | `auth seed/list/verify/delete` 서브커맨드 |
| `zero_touch_qa/executor.py` | ~25 | env 기반 viewport/locale/timezone/color_scheme override |
| `recording_service/server.py` | ~140 | `/auth/profiles*` 엔드포인트 5~6개 + `/recording/start` 인자 + verify 게이트 + extra_args glue |
| `recording_service/codegen_runner.py` | ~5 | (`extra_args` 그대로 — glue 만 server 측) |
| `recording_service/replay_proxy.py` | ~30 | `run_llm_play` / `run_codegen_replay` 가 metadata.json 읽고 storage + fingerprint env 주입 |
| `recording_service/converter_proxy.py` | ~25 | `original.py` storage 경로 → env var 치환 (D3) |
| `recording_service/storage.py` | ~10 | metadata.json 에 auth_profile 필드 추가 |
| `recording_service/web/index.html` | ~50 | 인증 블록 + 모달 4개 |
| `recording_service/web/app.js` | ~220 | 드롭다운/시드/verify/모달 + 머신 일치 검사 |
| `recording_service/web/style.css` | ~50 | 스타일 |
| `requirements.txt` | ~1 | `playwright>=1.54` (D14) |

**총 약 1200~1400 LOC**. 단일 PR.

### 5.5 핵심 함수 시그니처 (v2)

`zero_touch_qa/auth_profiles.py`:
```python
ROOT = Path(os.environ.get("AUTH_PROFILES_DIR", "~/ttc-allinone-data/auth-profiles")).expanduser()

@dataclass
class FingerprintProfile:
    """D10. UA 는 capture-only — 임의 spoof 안 함."""
    viewport_width: int
    viewport_height: int
    locale: str                          # ex) "ko-KR"
    timezone_id: str                     # ex) "Asia/Seoul"
    color_scheme: str = "light"          # "light" / "dark"
    playwright_version: str = ""         # 시드 시점 capture
    playwright_channel: str = "chromium" # "chromium" / "chrome" / "msedge"
    captured_user_agent: str = ""        # 시드 시점 capture (informational)

    def to_playwright_open_args(self) -> list[str]:
        """playwright open / codegen CLI 옵션 (UA 제외)."""
        return [
            "--viewport-size", f"{self.viewport_width},{self.viewport_height}",
            "--lang", self.locale,
            "--timezone", self.timezone_id,
            "--color-scheme", self.color_scheme,
        ]

    def to_browser_context_kwargs(self) -> dict:
        """Playwright Python BrowserContext kwargs (재생/verify 용)."""
        return {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
        }

    def to_env(self) -> dict:
        """env var 변환 (replay_proxy → executor)."""
        return {
            "PLAYWRIGHT_VIEWPORT": f"{self.viewport_width}x{self.viewport_height}",
            "PLAYWRIGHT_LOCALE": self.locale,
            "PLAYWRIGHT_TIMEZONE": self.timezone_id,
            "PLAYWRIGHT_COLOR_SCHEME": self.color_scheme,
        }

@dataclass
class NaverProbeSpec:
    """D13. weak negative check."""
    url: str = "https://nid.naver.com/"
    kind: str = "login_form_negative"   # 다른 종류 추가 시 enum 으로
    selector: str = "input[name='id']"  # 이게 보이면 로그아웃 상태

@dataclass
class VerifySpec:
    service_url: str                              # 필수 — authoritative
    service_text: str = ""                        # 선택 — 있으면 강검증, 없으면 URL 접근 약검증
    naver_probe: Optional[NaverProbeSpec] = None  # 선택 — best-effort

@dataclass
class AuthProfile:
    name: str
    service_domain: str
    storage_path: Path
    created_at: str
    last_verified_at: Optional[str]
    ttl_hint_hours: int
    verify: VerifySpec
    fingerprint: FingerprintProfile
    host_machine_id: str
    chips_supported: bool
    session_storage_warning: bool
    verify_history: list[dict]
    notes: str

# ── CRUD ────────────────────────────────────────────────────────────
def list_profiles() -> list[AuthProfile]: ...
def get_profile(name: str) -> AuthProfile: ...
def delete_profile(name: str) -> None: ...

# ── Sanitize / Identity ─────────────────────────────────────────────
def _validate_name(name: str) -> None:
    """^[a-zA-Z0-9_\\-]+$ 만 허용. 위반 시 ValueError."""

def current_machine_id() -> str:
    """hostname + 디스크 UUID 해시. 안정적 / 같은 머신 재실행 시 동일."""

def current_playwright_version() -> str:
    """`playwright --version` 파싱."""

# ── Lifecycle ──────────────────────────────────────────────────────
def seed_profile(
    name: str,
    seed_url: str,
    verify: VerifySpec,
    *,
    service_domain: Optional[str] = None,         # seed_url 에서 자동 유추
    fingerprint: Optional[FingerprintProfile] = None,  # None → 기본값 (1280x800/ko-KR/Asia/Seoul/light)
    ttl_hint_hours: int = 12,
    notes: str = "",
    timeout_sec: int = 600,
) -> AuthProfile:
    """playwright open <seed_url> --save-storage=<path> + fingerprint args.
    창 닫힘 감지 → dump 검증 → sessionStorage detection → verify_profile → 등록.
    실패 시 storage 파일 삭제."""

def verify_profile(
    profile: AuthProfile,
    *,
    timeout_sec: int = 30,
    naver_probe: bool = True,            # False 면 service-only
) -> tuple[bool, dict]:
    """D13. service-side authoritative + naver-side optional weak.
    headed (D9) + fingerprint (D10).
    반환: (ok, detail) — detail = {service_ms, naver_probe_ms, fail_reason, ...}.
    last_verified_at 갱신 + verify_history append (last 20)."""

# ── Validation helpers ─────────────────────────────────────────────
class MissingDomainError(ValueError): ...
class EmptyDumpError(ValueError): ...
class ChipsNotSupportedError(RuntimeError): ...

def validate_dump(storage_path: Path, expected_domains: list[str]) -> None:
    """D12. cookies 비어있음 → EmptyDumpError.
    각 expected_domain 마다 endsWith 매칭 쿠키 1개 이상 → 없으면 MissingDomainError."""

def has_partitioned_cookies(storage_path: Path) -> bool: ...

def detect_session_storage_use(storage_path: Path) -> bool:
    """D16. 시드 시점에 별도로 capture 한 sessionStorage 키들에서
    'token'/'auth'/'session' 의심 키 발견 시 True."""

def chips_supported_by_runtime() -> bool:
    """D14. installed Playwright >= 1.54 인지."""

# ── Internal storage helpers ────────────────────────────────────────
def _dump_storage_state(context, path: Path) -> None:
    """Playwright BrowserContext.storage_state(path=path, indexed_db=True).
    1.51+ 에서 indexed_db 지원."""
```

`zero_touch_qa/__main__.py` (CLI 진입점):

```bash
# 시드
python3 -m zero_touch_qa auth seed \
    --name booking-via-naver \
    --seed-url https://booking.example.com/ \
    --verify-service-url https://booking.example.com/mypage \
    [--verify-service-text "김QA님 환영합니다"] \
    [--no-naver-probe] \
    --ttl-hint-hours 12

# 목록
python3 -m zero_touch_qa auth list

# 검증
python3 -m zero_touch_qa auth verify --name booking-via-naver

# 삭제
python3 -m zero_touch_qa auth delete --name booking-via-naver
```

### 5.6 Recording 서버 신규 엔드포인트

| 엔드포인트 | 역할 |
|---|---|
| `GET /auth/profiles` | 카탈로그 목록 (UI 드롭다운용) |
| `POST /auth/profiles/seed` | 시드 시작. `{name, seed_url, verify, ...}` → subprocess 시작 + sub-session id 반환 |
| `GET /auth/profiles/seed/{seed_sid}` | 시드 진행 폴링. `state: waiting_user / verifying / ready / error` |
| `POST /auth/profiles/{name}/verify` | 명시적 verify 트리거 |
| `DELETE /auth/profiles/{name}` | 삭제 |

`POST /recording/start` 모델 확장:
```python
class RecordingStartReq(BaseModel):
    target_url: str
    planning_doc_ref: Optional[str] = None
    auth_profile: Optional[str] = None     # NEW
```

`/recording/start` 흐름:
```python
extra_args: list[str] = []
warning_headers: dict = {}
if req.auth_profile:
    prof = auth_profiles.get_profile(req.auth_profile)
    ok, detail = auth_profiles.verify_profile(prof)
    if not ok:
        raise HTTPException(409, detail={"reason": "profile_expired", **detail})
    if prof.host_machine_id != auth_profiles.current_machine_id():
        warning_headers["X-Auth-Machine-Mismatch"] = "1"
    extra_args += ["--load-storage", str(prof.storage_path)]
    extra_args += prof.fingerprint.to_playwright_open_args()
    sess.extras["auth_profile"] = prof.name

# storage.save_metadata 시 auth_profile 포함 (D15 — scenario.json 미수정)
storage.save_metadata(sess.id, {
    ...,
    "auth_profile": req.auth_profile,
})

handle = _start_codegen_impl(
    target_url=req.target_url,
    output_path=output_path,
    timeout_sec=...,
    extra_args=extra_args,        # ← server.py 의 _start_codegen_impl 시그니처 확장 필요
)
```

### 5.7 `original.py` 의 storage 경로 치환 (D3)

`converter_proxy` 변환 *후* 후처리:
```python
def portabilize_storage_path(py_path: Path) -> bool:
    """codegen 이 박은 절대 경로 storage_state="..." 를 env var 로 치환.
    다른 머신/경로에서도 이식 가능. 변경 일어나면 True."""
    text = py_path.read_text(encoding="utf-8")
    pattern = re.compile(r'storage_state\s*=\s*r?["\']([^"\']+)["\']')
    if not pattern.search(text):
        return False
    new_text = pattern.sub(
        'storage_state=os.environ["AUTH_STORAGE_STATE_IN"]', text
    )
    if "import os" not in new_text:
        # codegen 출력은 보통 import 부 앞쪽에 두므로 가장 위에 삽입
        new_text = "import os\n" + new_text
    py_path.write_text(new_text, encoding="utf-8")
    return True
```

재생 시 `replay_proxy.run_codegen_replay` 가 `AUTH_STORAGE_STATE_IN` env 주입 → 절대 경로 의존 제거.

### 5.8 재생 (LLM 모드 + 원본 .py 모드) 변경

**`replay_proxy.run_llm_play`**:
```python
# 세션 디렉토리의 metadata.json 에서 auth_profile 읽음 (D15)
meta_path = Path(host_session_dir) / "metadata.json"
meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
auth_profile = meta.get("auth_profile")

env = os.environ.copy()
if auth_profile:
    prof = auth_profiles.get_profile(auth_profile)
    ok, detail = auth_profiles.verify_profile(prof)
    if not ok:
        raise ReplayProxyError(f"profile expired: {detail}")
    cmd += ["--storage-state-in", str(prof.storage_path)]
    env.update(prof.fingerprint.to_env())   # PLAYWRIGHT_VIEWPORT/_LOCALE/_TIMEZONE/_COLOR_SCHEME
```

**`replay_proxy.run_codegen_replay`** (원본 .py 직접 실행):
```python
env = os.environ.copy()
if auth_profile:
    prof = auth_profiles.get_profile(auth_profile)
    env["AUTH_STORAGE_STATE_IN"] = str(prof.storage_path)  # portabilize 한 .py 가 읽는 키
    env.update(prof.fingerprint.to_env())
```

**`zero_touch_qa.executor.execute`**:
```python
# 기존 context_kwargs 기본값을 env override 로 보강 (D10 fingerprint 통일)
viewport_env = os.environ.get("PLAYWRIGHT_VIEWPORT")  # "1280x800"
locale_env = os.environ.get("PLAYWRIGHT_LOCALE")
timezone_env = os.environ.get("PLAYWRIGHT_TIMEZONE")
color_env = os.environ.get("PLAYWRIGHT_COLOR_SCHEME")

if viewport_env and "x" in viewport_env:
    w, h = viewport_env.split("x", 1)
    context_kwargs["viewport"] = {"width": int(w), "height": int(h)}
if locale_env:
    context_kwargs["locale"] = locale_env
if timezone_env:
    context_kwargs["timezone_id"] = timezone_env
if color_env:
    context_kwargs["color_scheme"] = color_env
```

> UA 는 env 로 받지 않음 — D10 정책. Playwright 버전·채널 일치로 자연 해소.

---

## 6. 보강책 7개 (외부 audit 결과 반영, v2 정정)

| # | 보강책 | 어디서 구현 | 의사결정 |
|---|---|---|---|
| 1 | Fingerprint pinning (UA 제외 — viewport/locale/timezone/color_scheme + Playwright 버전·채널) | 5.5 `FingerprintProfile` + 5.8 env 주입 | D10 (v2) |
| 2 | Same-machine 강제 (불일치 시 빨간 경고) | 5.5 `current_machine_id` + 4.3 모달 | D11 |
| 3 | 재생도 headed (headless 옵션 X, 첫 릴리스) | 5.8 + 시드/verify 동일 | D9 |
| 4 | Dump 검증 — 양 도메인 쿠키 강제 (endsWith 매칭) | 5.5 `validate_dump` | D12 |
| 5 | Dual verify — service authoritative + naver weak probe (optional) | 5.5 `verify_profile` + `NaverProbeSpec` | D13 (v2) |
| 6 | Re-seed cadence 메트릭 가시화 | `verify_history` (last 20) + UI 노출 | (신규) |
| 7 | CHIPS guard — Playwright 버전 게이트 (≥1.54 OK) | 5.5 `chips_supported_by_runtime` | D14 (v2) |
| 8 | sessionStorage detection — 시드 시 의심 키 감지 후 경고 | 5.5 `detect_session_storage_use` | D16 (v2 신설) |

---

## 7. 검증 / 테스트 전략

### 7.1 단위 테스트 (`test/test_auth_profiles.py`)

| 케이스 | 검증 |
|---|---|
| `seed_profile` 정상 흐름 (`playwright open` 모킹) | 카탈로그 등록 + 0600 + dump 검증 통과 |
| seed 도중 사용자가 빈 창 닫음 | dump 빈 파일 → `EmptyDumpError` + storage 삭제 |
| seed 결과에 한 도메인 쿠키 누락 | `validate_dump` 가 `MissingDomainError` |
| `verify_profile` — 서비스 텍스트 발견 / 못 찾음 | True / False |
| `verify_profile` — naver probe 통과 / 실패 (warn-only) | service 통과 시 ok=True (probe 실패는 detail 만) |
| `verify_profile` — naver_probe=None (service-only) | naver 호출 안 함 |
| 이름 sanitize (`../foo`, `;rm -rf`, 한글) | `^[a-zA-Z0-9_\-]+$` 만 허용 |
| `_index.json` 동시 쓰기 | `fcntl.flock` 직렬화 |
| 권한 비트 (`stat.S_IMODE`) | storage 0600 + 디렉토리 0700 |
| `current_machine_id` 안정성 | 같은 머신 두 번 호출 시 동일 |
| `has_partitioned_cookies` | Partitioned 속성 1개라도 있으면 True |
| `chips_supported_by_runtime` | Playwright 1.54 / 1.57 / 1.50 mocking 으로 True/True/False |
| `detect_session_storage_use` | 'token'/'auth'/'session' 키 감지 vs 클린 |
| `validate_dump` 도메인 endsWith | `.naver.com`, `naver.com`, `accounts.naver.com` 모두 매칭 |
| `FingerprintProfile.to_playwright_open_args` | UA 옵션 미포함, viewport-size 콤마 형식 |

### 7.2 통합 테스트

| 케이스 | 검증 |
|---|---|
| `/auth/profiles/seed` API → polling → ready | end-to-end 모킹 |
| `/recording/start` + `auth_profile` 지정 → codegen extra_args 에 `--load-storage` 포함 | subprocess argv assert |
| `/recording/start` + 만료된 프로파일 | 409 + detail.reason="profile_expired" |
| `/recording/start` + 머신 불일치 | 200 + `X-Auth-Machine-Mismatch: 1` 헤더 |
| 변환된 `original.py` 의 storage 경로 = env var 형태 | 정규식 매칭 |
| 재생 wrapper 가 `AUTH_STORAGE_STATE_IN` + `PLAYWRIGHT_*` 설정 | env assert |
| 세션 metadata.json 에 auth_profile 박힘 (scenario.json 은 list 그대로) | JSON shape assert |
| executor 가 `PLAYWRIGHT_VIEWPORT/LOCALE/TIMEZONE/COLOR_SCHEME` env override 적용 | context_kwargs assert |

### 7.3 수동 회귀 (시드 단계는 사람이 들어가야 함)

| 시나리오 | 통과 기준 |
|---|---|
| Naver tester 계정으로 시드 1회 | 12시간 이내 verify 통과 유지 |
| 같은 머신에서 다음날 녹화 | 자동 verify 통과 + 코드젠 시작시 네이버 화면 미노출 |
| storage 파일 강제 삭제 후 Start | 만료 모달 노출 |
| 다른 머신에 storage 복사 후 Start | 머신 불일치 모달 노출 |
| Partitioned 쿠키 사용 서비스 (가공) 시드 — Playwright 1.54+ | 시드 통과 + dump 에 partition_key 보존 |
| sessionStorage 인증 의심 서비스 시드 | 등록 통과 + 노란 경고 표시 |

---

## 8. 보안 체크리스트

- [ ] storage 파일 0600, 디렉토리 0700 강제
- [ ] `auth-profiles/` `.gitignore`
- [ ] 로그/스크린샷에 storage 경로/쿠키값 노출 없음 (기존 `mask_secret` 헬퍼 재사용)
- [ ] 프로파일 이름 path traversal 방지 (`^[a-zA-Z0-9_\-]+$`)
- [ ] verify 실패 시 storage 자동 삭제 옵션
- [ ] 운영 권고: 테스트 전용 네이버 계정만 사용 (UI 모달에 명시)
- [ ] CLI 첫 사용 시 1회 동의 ("운영 계정 사용 금지" 체크박스)
- [ ] 백업 / artifact / CI cache 에 `auth-profiles/` 포함되지 않게 명시
- [ ] storage dump JSON 자체에 PII 가 들어갈 수 있음 — UI 노출 시 절대 경로/쿠키값 마스킹

---

## 9. Phase 분할 + 구체 Task 목록

각 task 는 *원자적 단위* — 한 PR 의 한 commit 으로 분해 가능. ID = 의존 그래프의 키.

### P1 — `auth_profiles.py` 핵심 모듈

| ID | 작업 | 파일 | 추가/변경 | 단위 테스트 | 통과 기준 | 의존 |
|---|---|---|---|---|---|---|
| **P1.1** | 디렉토리/스키마 헬퍼 + 이름 sanitize + index 락 | `zero_touch_qa/auth_profiles.py` (신규) | `ROOT`, `_index_path`, `_storage_path(name)`, `_load_index`, `_save_index` (`fcntl.flock`), `_validate_name` | 빈 index round-trip / 동시 쓰기 / sanitize 케이스 6개 | `pytest -k "index or sanitize"` PASS | — |
| **P1.2** | Dataclass 정의 + 직렬화 | 동상 | `FingerprintProfile`, `NaverProbeSpec`, `VerifySpec`, `AuthProfile` + `to_dict`/`from_dict` | round-trip 동등성 + 부분 dict 로드 | `pytest -k "dataclass"` PASS | P1.1 |
| **P1.3** | CRUD (list/get/delete) | 동상 | `list_profiles`, `get_profile(name)`, `delete_profile(name)` | 빈/단일/다중, 없는 name 삭제 | `pytest -k "crud"` PASS | P1.2 |
| **P1.4** | 머신 ID + Playwright 버전 헬퍼 | 동상 | `current_machine_id()`, `current_playwright_version()`, `chips_supported_by_runtime()` | 같은 호스트 안정 / 1.54/1.57/1.50 mock | `pytest -k "machine or chips"` PASS | P1.1 |
| **P1.5** | Dump 검증 + 부수 검사 | 동상 | `validate_dump(path, expected_domains)`, `has_partitioned_cookies(path)`, `detect_session_storage_use(path)` + 예외 클래스 | 합성 storage JSON 6종 (정상/빈/도메인누락/partitioned/세션의심/혼합) | `pytest -k "dump"` PASS | P1.2 |
| **P1.6** | Verify (service + optional naver probe) | 동상 | `verify_profile(profile, *, timeout_sec, naver_probe=True)` | 로컬 fastapi fixture 로 4 케이스 (service ok/fail × probe ok/fail) | fixture 기반 e2e PASS | P1.2 P1.4 |
| **P1.7** | Seed 라이프사이클 | 동상 | `seed_profile(...)` — Popen `playwright open` + dump 검증 + sessionStorage detect + verify + 등록 | `playwright open` 모킹: 정상/빈dump/도메인누락/verify실패 | 실패 시 storage 삭제 검증 / 등록 시 fingerprint capture 확인 | P1.5 P1.6 P2.0 (env 변수 기본값) |

**P1 마감 기준**: 모든 단위 테스트 PASS + `python3 -c "from zero_touch_qa import auth_profiles; print(auth_profiles.list_profiles())"` 실행 가능.

### P2 — CLI 진입점

| ID | 작업 | 파일 | 추가/변경 | 단위 테스트 | 통과 기준 | 의존 |
|---|---|---|---|---|---|---|
| **P2.1** | `auth` 서브파서 추가 | `zero_touch_qa/__main__.py` | argparse subparser `auth` + 4개 sub-sub (`seed`/`list`/`verify`/`delete`) | argparse 스모크 — `--help` rc=0 | help 출력 정상 | P1.3 |
| **P2.2** | `auth seed` 와이어 | 동상 | `seed` 핸들러 — `seed_profile()` 호출 + 로그 | (사람이 직접 시드 — 모킹 단위 테스트는 P1.7 이미 커버) | 수동 스모크 1회 통과 | P1.7 P2.1 |
| **P2.3** | `auth list/verify/delete` 와이어 | 동상 | 3개 핸들러 + 사람 친화 출력 (last_verified_at 상대시간 등) | argparse 스모크 + verify mock | rc=0 / 0/N개 출력 정확성 | P1.3 P1.6 P2.1 |
| **P2.4** | `auth verify --json` 옵션 | 동상 | JSON 출력 모드 (스크립트 통합용) | snapshot test | JSON shape 안정 | P2.3 |

**P2 마감 기준**: `python3 -m zero_touch_qa auth list` 가 빈 카탈로그에서 PASS, `auth seed --help` 가 모든 인자 노출.

### P3 — Recording 서버 통합

| ID | 작업 | 파일 | 추가/변경 | 단위 테스트 | 통과 기준 | 의존 |
|---|---|---|---|---|---|---|
| **P3.1** | `_start_codegen_impl` 시그니처 확장 — `extra_args` 받아 `start_codegen` 으로 전달 | `recording_service/server.py:83` | `def _start_codegen_impl(target_url, output_path, *, timeout_sec, extra_args=None)` + 기존 호출부 갱신 | 기존 recording-e2e 테스트 + 신규 argv assert | argv 에 extra_args 들어감 | — |
| **P3.2** | `GET /auth/profiles` 엔드포인트 | 동상 | 카탈로그 → JSON (last_verified_at 상대시간 포함) | API e2e | 200 + 빈 list / 단일 / 다중 | P1.3 |
| **P3.3** | `POST /auth/profiles/seed` + 진행 폴링 | 동상 + 신규 in-memory `seed_registry` | 시드 subprocess 시작 + sub-session id 반환. state 머신: `waiting_user → verifying → ready / error` | API e2e (subprocess 모킹) | 폴링으로 모든 state 관찰됨 | P1.7 |
| **P3.4** | `GET /auth/profiles/seed/{seed_sid}` | 동상 | seed 진행 상태 반환 | 동상 | 상태 정확 + done 시 profile name 반환 | P3.3 |
| **P3.5** | `POST /auth/profiles/{name}/verify` | 동상 | `verify_profile()` → JSON | API e2e | ok/fail/detail | P1.6 |
| **P3.6** | `DELETE /auth/profiles/{name}` | 동상 | `delete_profile()` | API e2e | 204 / 404 | P1.3 |
| **P3.7** | `RecordingStartReq.auth_profile` 필드 + verify 게이트 + extra_args 빌드 + machine_id 검사 | 동상 | `recording_start()` 변경 — 만료 시 409, 머신 불일치 시 200 + `X-Auth-Machine-Mismatch` 헤더 | API e2e (정상/만료/불일치) | 모든 케이스 커버 | P3.1 P1.6 |
| **P3.8** | session metadata.json 에 `auth_profile` 필드 (D15) | `recording_service/storage.py` + `server.py` 호출부 | `save_metadata` 호출 시 `auth_profile` 포함, scenario.json 은 변경 없음 | round-trip 테스트 | metadata 에는 있고 scenario 에는 없음 | P3.7 |
| **P3.9** | `original.py` 경로 치환 (D3) | `recording_service/converter_proxy.py` (또는 신규 `post_process.py`) | `portabilize_storage_path(py_path)` — regex + import os 보장 | 합성 codegen 출력 4종 (storage_state 있음/없음/이미 env 사용/multi-line) | 정규식 정확성 | — |
| **P3.10** | 변환 흐름에 P3.9 hook | `recording_service/converter_proxy.py` 호출부 (server.py 의 변환 후) | convert 성공 후 portabilize 호출 | 통합 테스트 | original.py 가 env 형태로 변환됨 | P3.9 |

**P3 마감 기준**: API 통합 테스트 모두 PASS + 손으로 한 번 시드 → 녹화 → metadata.json 확인.

### P4 — 재생 자동 매칭

| ID | 작업 | 파일 | 추가/변경 | 단위 테스트 | 통과 기준 | 의존 |
|---|---|---|---|---|---|---|
| **P4.1** | `executor.execute` 에 env override 추가 | `zero_touch_qa/executor.py:241+` | env 4종 (`PLAYWRIGHT_VIEWPORT/_LOCALE/_TIMEZONE/_COLOR_SCHEME`) → context_kwargs override (없으면 기존 기본값) | env 있음/없음/일부 — context_kwargs assert | 기존 회귀 통과 + 신규 매칭 | — |
| **P4.2** | `replay_proxy.run_llm_play` — metadata.json → storage + env | `recording_service/replay_proxy.py:156` | meta 로드 → auth_profile → verify → cmd `--storage-state-in` + env 주입 | 합성 세션 디렉토리 fixture | argv + env assert | P1.6 P3.8 P4.1 |
| **P4.3** | `replay_proxy.run_codegen_replay` — env 주입 | 동상 (`run_codegen_replay`) | meta 로드 → `AUTH_STORAGE_STATE_IN` + fingerprint env | 동상 | env assert | P3.9 P4.2 |
| **P4.4** | LLM 재생 만료 처리 | 동상 | verify 실패 시 `ReplayProxyError` + UI 가 만료 모달로 핸들 | 통합 테스트 (만료된 세션 재생) | 4xx 가 UI 까지 전달 | P4.2 |

**P4 마감 기준**: 녹화 → 재생 라운드트립이 storage 자동 적용으로 PASS (수동 회귀 1회).

### P5 — UI

| ID | 작업 | 파일 | 추가/변경 | 검증 | 통과 기준 | 의존 |
|---|---|---|---|---|---|---|
| **P5.1** | 인증 블록 + 모달 4개 마크업 | `recording_service/web/index.html` | `<fieldset.auth-block>` + 4 dialog | HTML lint, console error 없음 | 시각 검수 | — |
| **P5.2** | 드롭다운 채우기 + 상태 라벨 | `recording_service/web/app.js` | `loadAuthProfiles()` — `GET /auth/profiles` → select / status | 수동 검수 | 시드된 프로파일 노출 | P3.2 P5.1 |
| **P5.3** | ↻ verify 버튼 동작 | 동상 | `POST /auth/profiles/{name}/verify` → 상태 업데이트 | 수동 검수 | 통과/실패 라벨 변경 | P3.5 P5.2 |
| **P5.4** | 시드 입력 모달 + 시작 | 동상 + `web/style.css` | `openSeedDialog()` → `POST /auth/profiles/seed` → 진행 모달로 전환 | 수동 검수 | 폼 검증 + 잘못 입력시 인라인 에러 | P3.3 P5.1 |
| **P5.5** | 시드 진행 폴링 | 동상 | 1초 폴링 → state 따라 안내 변경 → ready/error 처리 | 수동 검수 | 모든 state 시각화 | P3.4 P5.4 |
| **P5.6** | 만료 모달 (Start 시 409 핸들) | 동상 | submit 핸들러에서 catch → 모달 → [재시드] 누르면 prefill 된 시드 모달 | 수동 검수 | 재시드 후 자동 녹화 재개 | P3.7 P5.4 |
| **P5.7** | 머신 불일치 모달 (`X-Auth-Machine-Mismatch` 헤더 핸들) | 동상 | submit 응답 헤더 검사 → 경고 모달 → [그래도 시도] 버튼 | 수동 검수 | 차단 안 함, 사용자 자각만 | P3.7 |
| **P5.8** | 결과 카드 메타 + 세션 테이블 컬럼 | `index.html` + `app.js` | `<dt>인증 프로파일</dt>` + 테이블 `auth` 컬럼 | 수동 검수 | 시각 검수 | P3.8 |
| **P5.9** | sessionStorage 경고 표시 | `app.js` | profile 의 `session_storage_warning=true` 면 노란 라벨 | 수동 검수 | 경고 노출 | P5.2 |
| **P5.10** | UI 스타일 정합 | `web/style.css` | 기존 `.card` 패턴 재사용 + 모달 색감 | 수동 검수 | 시각적 일관성 | P5.1 |

**P5 마감 기준**: 처음 사용자가 도움말 없이 시드 → 녹화 → 재생 풀 사이클 완주 (수동 사용성 검증 1회).

### P6 — 문서

| ID | 작업 | 파일 | 추가/변경 | 통과 기준 | 의존 |
|---|---|---|---|---|---|
| **P6.1** | 사용자 가이드 — Naver 예시 + 보안 + 한계 | `docs/auth-profile-usage.md` (신규) | 시드~재생 완주 워크스루 + 한계 5개 명시 + 트러블슈팅 | 첫 사용자가 읽고 시드 성공 | P1~P5 |
| **P6.2** | architecture.md 단락 추가 | `architecture.md` | auth-profile 흐름 + 기존 auth_login DSL 와의 관계 | 리뷰 통과 | P6.1 |
| **P6.3** | README.md Recording UI 섹션 1줄 추가 | `README.md` | "인증 프로파일로 외부 IdP 통과" 한 줄 | 리뷰 통과 | P6.1 |
| **P6.4** | docs/auth-login-usage.md 와의 차이 명시 | `docs/auth-login-usage.md` 끝부분 또는 P6.1 안에 cross-link | 두 시스템의 적용 영역 비교표 | 리뷰 통과 | P6.1 |

**P6 마감 기준**: 신규 입사 QA 가 P6.1 만 읽고 시드 → 녹화 → 재생을 외부 도움 없이 성공.

### P 종합

| Phase | Task 수 | 의존 entry point | 주요 산출물 |
|---|---:|---|---|
| P1 | 7 | (없음) | `auth_profiles.py` 모듈 + 단위 테스트 |
| P2 | 4 | P1.3 / P1.6 / P1.7 | CLI 4 sub-sub |
| P3 | 10 | P1.x | 6 endpoints + recording_start 변경 + 변환 후처리 |
| P4 | 4 | P3.8 / P1.6 | 재생 자동 매칭 |
| P5 | 10 | P3.2~P3.7 | UI |
| P6 | 4 | P5 | 문서 |
| **합계** | **39** | — | — |

**병렬 가능성**: P3.9 P3.10 (변환 후처리) 는 P1 과 무관 → 동시 진행 가능. P5.10 (스타일) 도 P5.1 만 끝나면 병렬. 그 외는 의존 그래프 따라 순차.

**예상 LOC 분포**: P1 ~520 / P2 ~80 / P3 ~280 / P4 ~75 / P5 ~320 / P6 ~350 / 테스트 ~300 = 총 ~1900 LOC (테스트 포함).

---

## 10. 비-목표 (의도적으로 안 함)

- 네이버 2중 확인 자동 통과 (CAPTCHA / SMS / 디바이스 인증)
- 만료된 세션 자동 갱신 — 사람이 재시드
- Headless 재생 (D9)
- 다중 머신 / CI 회전 IP 환경 안정 동작 (D7)
- 네이버 외 IdP (카카오, 구글 등) 1차 검증 — 같은 인프라로 가능하나 첫 릴리스 안 함
- OAuth callback mock (별 트랙)
- 테스트 전용 세션 생성 API (별 트랙)
- 시드한 머신 ↔ 다른 머신 자동 동기화 — 의도적으로 *수동 재시드* 가 더 안전
- ~~Partitioned/CHIPS 쿠키 가진 서비스~~ → **D14 정정**: Playwright ≥1.54 면 지원. 미만이면 거절
- sessionStorage 단독 의존 인증 서비스 — detection 만 하고 사용자 경고 (§12 fallback 트랙)

---

## 11. 한계 (사용자가 *수용*해야 할 것)

이 한계들은 설계 결함이 아니라 *외부 환경의 통제 불가능성에서 오는* 본질적
제약. 운영 시 사용자에게 명시적으로 안내한다.

1. **재시드를 "일 1회 floor" 로 가정한다.** 며칠~주 단위는 운에 가깝다.
2. **시드 머신 = 재생 머신.** 다른 머신으로 옮기면 사실상 매번 재시드.
3. **Jenkins / 공유 CI 안정 회귀로는 부적합.** 그게 목표면 callback mock 필요.
4. **개인 네이버 계정으로 시드하면 막힐 가능성↑.** 시드용 신규 테스트 계정 권장.
5. **이 패턴의 공개 사례 0건.** 우리가 1호 — 유지보수 리스크 인지.
6. **(v2 신설) 서비스가 OAuth `auth_type=reauthenticate` 또는 자체 step-up auth 를 강제하는 민감 페이지 (결제·탈퇴·개인정보 변경 등) 는 storage 재사용으로 우회 안 됨.** 시나리오 분할 또는 해당 step 만 수동 수행 권장.
7. **(v2 신설) 서비스가 인증 토큰을 sessionStorage 에 두면 storage_state 단독으로 보존 안 됨.** 시드 시 detection 으로 경고. fallback 은 §12 의 persistent userDataDir 트랙.

---

## 12. 후속 트랙 (이번 PR scope 외)

| 트랙 | 트리거 |
|---|---|
| OAuth callback mock | 서비스 백엔드 협조 가능 시 |
| 테스트 세션 생성 API | 서비스 백엔드 협조 가능 시 |
| 카카오 / 구글 IdP 검증 | 네이버 패턴 안정 후 |
| **(v2 신설) Persistent userDataDir fallback** | sessionStorage 의존 서비스 운영 필요 시. `BrowserContext` 가 아닌 *전체 user-data-dir 디렉토리* 를 보존·복원 → sessionStorage 까지 살림. 보안/디스크 영향 큼 — 별도 스펙 필요 |
| 머신 불일치 자동 재시드 (헤드리스 백그라운드 시드) | 안 함 — 사용자 통제 보장 우선 |
| Jenkins 통합 (안정 회귀) | callback mock 트랙과 묶음 |

---

## 13. 의존 / 영향

### 13.1 외부 의존

- `playwright` CLI (`playwright open --save-storage`, `--load-storage`, `--viewport-size`, `--lang`, `--timezone`, `--color-scheme`) — 이미 toolchain 에 있음 (1.57.0 확인됨)
- 신규 패키지 추가 없음
- **(v2)** `requirements.txt` 의 `playwright>=1.51` → `>=1.54` 로 상향 (D14 — CHIPS 지원). Phase 0 핀이므로 별 PR 후보. 본 PR 에서 변경 시 회귀 테스트 1회 돌려야 함

### 13.2 영향받는 기존 모듈

- `zero_touch_qa.executor` — `--storage-state-in` 이미 있음. `PLAYWRIGHT_*` env 처리 추가 (P4.1)
- `recording_service.codegen_runner` — `extra_args` 이미 있음. 변경 거의 없음
- `recording_service.server._start_codegen_impl` — `extra_args` glue 추가 필요 (P3.1)
- `recording_service.converter_proxy` — 변환 후 경로 치환 1단계 추가 (P3.9~P3.10)
- `recording_service.replay_proxy` — 재생 cmd 빌드 변경 (P4.2~P4.3)
- `recording_service.storage` — metadata 에 `auth_profile` 필드 (P3.8)
- 기존 `auth_login` DSL — *별개*. 폼 로그인 / TOTP / oauth 모드는 그대로 둔다.
  본 plan 의 auth-profile 은 그것의 *대체* 가 아니라 *보완* — IdP 화면 자체를
  통과시키는 게 불가능한 시나리오용

### 13.3 문서 영향

- `docs/auth-login-usage.md` — 변경 없음. 별도 `docs/auth-profile-usage.md` 신설
- `architecture.md` — Phase 1 단락 추가 (auth-profile 세션 흐름)
- `README.md` — Recording UI 섹션에 auth-profile 1줄 추가

---

## 14. 결정 필요 항목 (P1.1 시작 직전 마지막 확인)

| # | 항목 | 옵션 | 추천 |
|---|---|---|---|
| Q1 | 검증 텍스트 입력 부담 | (a) 사용자가 직접 입력해 강검증 (b) 비워두고 URL 접근 약검증 | 기본은 (b) 허용 + (a) 권장. 로그인 페이지가 같은 도메인에서 200을 반환하면 (b)는 false positive 위험 |
| Q2 | 시드 시 사용자가 다른 OAuth (구글/카카오) 클릭 detection | (a) 안 함 (b) verify 단계 redirect chain 분석 | (a) — verify 가 어차피 service-side 검증으로 잡음 |
| Q3 | 네이버 probe 기본값 | (a) 활성 (b) 비활성 | (a) — 작은 추가 검증, 비용 ≪ 가치 |
| Q4 | sessionStorage detection 세부 정책 — 어떤 키 패턴으로 의심? | (a) `(?i)(token|auth|session|jwt|bearer)` 정규식 (b) 길이 ≥20 인 base64-like 값 (c) 둘 다 | (c) — false negative 줄임 |
| Q5 | `playwright>=1.54` 업그레이드를 본 PR 에 포함? | (a) 본 PR (b) 별 PR | (b) — Phase 0 핀이라 영향 큼. 본 PR 은 `chips_supported_by_runtime` 게이트만 추가 |
| Q6 | `original.py` portabilize 시점 | (a) 변환 직후 자동 (b) 사용자 명시 옵션 | (a) — 항상 안전한 변환 |
| Q7 | 시드 성공 후 자동 선택 여부 | (a) 자동 선택 (b) 사용자가 `사용하지 않음`/`이 프로파일 사용` 결정 | (b) — 저장 성공과 이번 녹화 적용을 분리해 사용자 통제 보장 |

---

## 15. 변경 이력

| 일자 | 버전 | 내용 |
|---|---|---|
| 2026-04-29 | v1 | 초안 — 사용자 결정 D1~D14 + 외부 feasibility audit 결과 반영 |
| 2026-04-29 | v2 | 외부 코드 리뷰 반영 (C1~C12) — D10/D13/D14 정정 + D15(metadata 분리)/D16(sessionStorage) 신설. §3.1 Step 5/7, §5.2 스키마, §5.5 함수 시그니처, §6 보강책, §10/§11 한계, §12 후속 트랙, §13 requirements, §14 결정 항목 갱신. §9 Phase 분할을 39개 atomic task 로 펼침 (P1.1~P6.4) |
| 2026-04-29 | v3 | **post-review fix (5건)** — (F1) `.gitignore` 에 `auth-profiles/` + `*.storage.json` 추가 (root). (F2) `recording_start` 의 `_resolve_auth_profile_extras()` 호출을 `_registry.create()` *전* 으로 이동 — auth 검증 실패 시 orphan pending 세션 잔존 회귀 차단 + e2e 회귀 추가. (F3) `rplus/router.py` 의 `play-codegen`/`play-llm` 에 `ReplayAuthExpiredError` 별도 catch → 502 가 아니라 409 + `detail.reason="profile_expired"` + `profile_name`. `app.js` 의 `_runPlay` 에 409 분기 추가해 `_showExpiredDialog` 호출 + e2e 회귀 추가. (F4) `recording_stop` 의 `save_metadata` 가 `auth_profile` 키를 silent-drop 하던 버그 — `_save_metadata_preserving_auth(sid, new_meta)` 헬퍼 도입해 done/error 3분기 모두 보존. (F5) 만료 모달 [재시드] prefill — `GET /auth/profiles/{name}` detail endpoint 신설 (`AuthProfileDetail` 모델), UI 가 verify_service_url/text/ttl/probe 까지 prefill (seed_url 은 verify_url 의 origin 으로 추정). D16 정정 — sessionStorage detection *함수만* 구현이고 seed 통합은 후속 트랙임을 명시 |
