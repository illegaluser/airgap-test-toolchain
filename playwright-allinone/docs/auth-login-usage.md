# `auth_login` DSL 액션 사용 가이드 (T-D / P0.1)

15 번째 14-DSL 액션 `auth_login` 의 실 사용 절차. form 로그인 / TOTP / (OAuth)
세 모드 + credential 환경변수 + storage_state 재사용 + 로그 마스킹 계약을 다룬다.

---

## 1. 빠른 시작

```bash
# credential 등록 (env var)
export AUTH_CRED_DEMO_USER="tester@example.com"
export AUTH_CRED_DEMO_PASS="S3cret-pass-1234"
export AUTH_CRED_DEMO_TOTP_SECRET="JBSWY3DPEHPK3PXP"   # Base32, TOTP 활성 시만

# scenario.json 일부 — auth_login 으로 form 로그인 + TOTP 후 후속 액션
[
  {"step": 1, "action": "navigate", "target": "", "value": "https://app.example.com/login", ...},
  {"step": 2, "action": "auth_login", "target": "form", "value": "demo", ...},
  {"step": 3, "action": "auth_login", "target": "totp", "value": "demo", ...},
  {"step": 4, "action": "click", "target": "role=link, name=대시보드", ...}
]

# 실행 (인증 결과를 storage_state 로 덤프)
python3 -m zero_touch_qa --mode execute --scenario scenario.json \
    --storage-state-out artifacts/session.json
```

후속 시나리오는 `--storage-state-in artifacts/session.json` 으로 재사용해
로그인 스킵 가능.

---

## 2. credential alias 환경변수

각 alias 마다 최대 3 개 키. 하나만 있어도 OK (셋 다 비어 있으면
`CredentialError`).

```bash
AUTH_CRED_<ALIAS_NORM>_USER          # 이메일 / 사용자명 (form 모드 필수)
AUTH_CRED_<ALIAS_NORM>_PASS          # 비밀번호 (form 모드 필수)
AUTH_CRED_<ALIAS_NORM>_TOTP_SECRET   # Base32 TOTP 시크릿 (totp 모드 필수)
```

`<ALIAS_NORM>` 은 alias 의 비-영숫자 문자를 `_` 로 정규화한 대문자 형식:

| alias 입력 | env var prefix |
|---|---|
| `demo` | `AUTH_CRED_DEMO_` |
| `prod-google` | `AUTH_CRED_PROD_GOOGLE_` |
| `dev.staging` | `AUTH_CRED_DEV_STAGING_` |

운영 권고:

- **로컬 개발**: `direnv` / `.envrc` 또는 `.env` 파일 (git ignore)
- **Jenkins**: `withCredentials([string(...)])` 블록으로 주입
- **컨테이너**: `docker run -e AUTH_CRED_DEMO_PASS=...` 또는 secret mount

---

## 3. 모드별 사용법

### 3.1 form 모드 — 자동 탐지

```json
{"action": "auth_login", "target": "form", "value": "demo"}
```

executor 가 다음 후보 selector 를 우선순위대로 시도해 첫 일치 사용:

- email/username: `input[type="email"]`, `input[autocomplete="username"]`,
  `input[name*="email" i]`, `input[name*="user" i]`, …
- password: `input[type="password"]`, `input[autocomplete="current-password"]`, …
- submit: `button[type="submit"]`, `button:has-text("Sign in")`,
  `button:has-text("로그인")`, …

표준 HTML 폼이면 대부분 자동 탐지로 충분.

### 3.2 form 모드 — explicit selector

자동 탐지가 실패하거나 (예: 비표준 form) ambiguity 가 우려되면 selector 명시:

```json
{
  "action": "auth_login",
  "target": "form, email_field=#email, password_field=#password, submit=#login-btn",
  "value": "demo"
}
```

### 3.3 totp 모드

```json
{"action": "auth_login", "target": "totp", "value": "demo"}
```

`pyotp.TOTP(secret).now()` 로 현재 시각 기준 6자리 코드 생성 → OTP 입력 필드
fill → (있으면) submit 클릭. submit 버튼 미발견 시 auto-submit form 으로
가정하고 그대로 진행.

OTP 입력 필드 자동 탐지 후보: `input[autocomplete="one-time-code"]`,
`input[name*="otp" i]`, `input[name*="code" i]`, `input[inputmode="numeric"]`.

명시하려면:

```json
{"action": "auth_login", "target": "totp, totp_field=#otp", "value": "demo"}
```

⚠️ **시간 동기화** — 호스트 시계가 30s 이상 어긋나면 TOTP 코드가 거부될 수
있음. NTP 활성 권고.

### 3.4 oauth 모드 *(T-D Phase 5 미완료)*

```json
{"action": "auth_login", "target": "oauth, provider=mock", "value": "demo"}
```

**현재 상태**: FAIL (의도적). OAuth mock 컨테이너 (oauth2-mock-server) 통합이
follow-up commit 으로 연기됨. 실 IdP (Google 등) 에 직접 연결하려면 form 모드
+ 수동 OAuth flow 시나리오로 우회.

---

## 4. storage_state 재사용

인증은 비싸고 (LLM 호출 / OTP 생성 / IdP 왕복) 자주 변하지 않으므로 한 번 인증
하고 결과를 dump 후 후속 시나리오에 재주입한다.

### 4.1 dump (인증 시나리오)

```bash
python3 -m zero_touch_qa --mode execute \
    --scenario auth_only.json \
    --storage-state-out /var/run/qa/sessions/demo.json
```

또는 env:

```bash
AUTH_STORAGE_STATE_OUT=/var/run/qa/sessions/demo.json \
  python3 -m zero_touch_qa --mode execute --scenario auth_only.json
```

종료 시 BrowserContext 의 cookies + localStorage + sessionStorage 가 JSON 으로
저장됨.

### 4.2 restore (실제 테스트 시나리오)

```bash
python3 -m zero_touch_qa --mode execute \
    --scenario actual_test.json \
    --storage-state-in /var/run/qa/sessions/demo.json
```

또는 env `AUTH_STORAGE_STATE_IN`. 시나리오 step 1 부터 이미 인증된 상태로 시작.
auth_login 액션 자체가 시나리오에서 빠져도 됨.

### 4.3 retention 정책

storage_state 파일은 *민감 자료* — 로그인 토큰을 포함한다. 운영 권고:

- 파일 권한 `0600` (사용자 read/write 전용)
- 정기 만료 (24h ~ 7d, 대상 시스템의 세션 정책에 맞춤)
- git ignore + 백업 제외

---

## 5. 로그 마스킹 계약

executor 는 모든 credential 관련 출력을 자동 마스킹:

```text
[Step 2] auth_login mode=form alias=demo user=*****************om pass=**** totp=<set>
```

- user/email: 끝 2자만 평문 (디버깅 친화)
- password: 전체 마스킹 (`mask_secret(value, keep=0)`)
- TOTP 시크릿: 노출 안 함 (`<set>` / `<empty>` 만 표시)
- TOTP 코드: 로그 출력 안 함
- StepResult 의 `value` 는 마스킹된 alias 형태로 저장

회귀 보장: `test/test_auth.py` 의 caplog 기반 회귀 3 케이스가 평문 sentinel
검색을 0 hit 보장. 새 로그 추가 시 마스킹 실수 없도록 동일 회귀 사용 권고.

---

## 6. 디버깅 가이드

### 6.1 credential 환경변수 누락

```text
CredentialError: alias 'demo' 의 credential 이 환경변수에 없음.
필요 키: AUTH_CRED_DEMO_USER / _PASS / _TOTP_SECRET
```

→ 위 3 키 중 적어도 1 개 export. alias 정규화 (대문자 + `_`) 확인.

### 6.2 form 필드 자동 탐지 실패

```text
RuntimeError: auth_login email/username field 자동 탐지 실패 — 후보: [...]
```

→ explicit selector 로 우회: `target="form, email_field=#myemail, ..."`.

### 6.3 TOTP 코드 거부

→ 호스트 시계 NTP 동기화 확인. 시크릿이 Base32 형식인지 확인 (대부분 16~32자
영대문자 + 2~7).

### 6.4 storage_state 무시됨

```text
[Auth] storage_state_in 파일 없음 — 새 컨텍스트로 진행
```

→ 경로 오타 또는 권한 문제. `ls -la <path>` 로 확인.

---

## 7. 보안 체크리스트

- [ ] credential env var 가 git / 로그 / CI artifact 에 노출 안 됨
- [ ] storage_state 파일 권한 0600 + git ignore
- [ ] TOTP 시크릿은 production 시스템에서 분리 (테스트 전용 계정 권장)
- [ ] OAuth credential 은 별도 staging 계정 (production 계정 절대 금지)
- [ ] caplog 기반 회귀가 새 로그 추가에도 통과하는지 확인

---

## 8. 알려진 제약 / Backlog

| 항목 | 상태 | 추적 |
|---|---|---|
| OAuth mock 서버 (oauth2-mock-server) 통합 | 미완료 | T-D Phase 5 |
| Jenkins Credentials seed 자동화 | 미완료 | provision.sh 추가 예정 |
| 실 IdP (Google / Azure AD) 검증 | 미완료 | 별도 운영 검증 |
| WebAuthn / Passkey | OUT | PLAN_PRODUCTION_READINESS.md §B1 |
| SMS OTP | OUT | PLAN_PRODUCTION_READINESS.md §B1 |
| reCAPTCHA / hCaptcha | OUT | 봇 차단 의도 — 도메인 협조 필요 |
| SAML / OIDC 사내 IdP | 별도 트랙 | 회사별 selector 작성 필요 |

---

## 9. 스크린샷 마스킹 (P0.1 #3)

`auth_login` 의 PASS / FAIL 스크린샷은 자동으로 입력 필드를 검정 박스로 마스킹한다 — Playwright `page.screenshot(mask=[locator, ...])` 사용.

| 모드 | 마스킹 대상 |
|---|---|
| form | email/username input + password input |
| totp | TOTP code input |
| oauth | (현재 미지원 — Phase 5 후 IdP 페이지 마스킹 정책 별도 결정) |

- 비밀번호는 브라우저가 ●● 로 표시하지만 username/email/TOTP 코드는 평문 표시되므로 마스킹이 필수
- 구버전 Playwright (`mask` 인자 미지원) 환경에서는 fail-secure — 스크린샷을 생략하고 빈 경로 반환 (자격증명 노출 방지)
- `submit` 후 navigation 으로 detached 된 locator 는 Playwright 가 no-op 처리

회귀 가드는 `test/test_auth.py::test_screenshot_masked_*` 3개.

## 10. CLI 검증 통과 (P0.1 #1)

`auth_login` 액션은 14대 표준 액션이 아닌 인증 보조 액션이지만, `_VALID_ACTIONS` 화이트리스트에 등록되어 `python3 -m zero_touch_qa --mode execute --scenario ...` 흐름의 `_validate_scenario` 를 정상 통과한다.

| step 필드 | 계약 |
|---|---|
| `action` | `"auth_login"` (소문자 고정) |
| `target` | `"form"` / `"totp"` / `"oauth"` 중 하나로 시작. `, key=val` 모디파이어 (`email_field=#x`, `password_field=#pw`, `submit=#login`, `totp_field=#otp`, `provider=mock`) 가 따라올 수 있음 |
| `value` | credential alias (필수 — 비어 있으면 `ScenarioValidationError`) |
| `description` | 자유 텍스트 |

Planner LLM 은 `auth_login` 을 emit 하지 않는다 — 사용자가 손작성 시나리오나 변환된 codegen 결과에 직접 step 을 추가해야 한다.

---

## 11. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-04-29 | 초안 작성 — T-D Phase 1~4,7 완료에 맞춘 사용자 가이드. OAuth mock 은 follow-up commit 후 §3.4 갱신 예정 |
| 2026-04-29 | P0.1 리뷰 후속 패치 §9~§10 추가 — 스크린샷 마스킹 + CLI 검증 통과 명시 |
