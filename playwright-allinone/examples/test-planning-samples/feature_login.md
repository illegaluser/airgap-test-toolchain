# 사용자 로그인 기능 — 사용자 시나리오

## 페르소나

- **일반 사용자 (User)**: 가입된 이메일 / 비밀번호로 로그인.
- **관리자 (Admin)**: 추가로 2FA (OTP) 입력 필요.

## 메인 시나리오

### MS-1. 정상 로그인 (User)

1. 사용자가 `/login` 접근.
2. `#email` 에 등록된 이메일 입력 (예: `user@example.com`).
3. `#password` 에 정확한 비밀번호 입력.
4. `#login-btn` (텍스트 "로그인") 클릭.
5. 시스템이 인증 후 `/dashboard` 로 redirect.
6. 우측 상단에 사용자 이름 표시 (`#user-greeting` = "안녕하세요, <이름>님").

### MS-2. 정상 로그인 (Admin)

1. MS-1 의 1~4 단계와 동일.
2. 시스템이 OTP 입력 페이지로 redirect.
3. `#otp` 입력란에 6자리 OTP 입력.
4. `#otp-submit` (텍스트 "확인") 클릭.
5. `/admin` 페이지로 redirect.

## 대안 시나리오

### AS-1. 잘못된 비밀번호

- `#password` 에 잘못된 값 입력 → `#login-btn` 클릭.
- `#error-message` (role=alert) 에 "이메일 또는 비밀번호가 올바르지 않습니다" 표시.
- 페이지 URL 유지 (`/login`).

### AS-2. 미등록 이메일

- `#email` 에 미등록 주소 입력 → 같은 에러 표시 (보안상 "이메일 미등록" 직접 표기 금지).

### AS-3. 5회 연속 실패 시 계정 잠금

- 동일 이메일로 5회 연속 비밀번호 오류 → 계정 30분 잠금.
- `#error-message` 에 "계정이 잠겼습니다. 30분 후 다시 시도하세요" 표시.

## 화면 요소 (id 기준)

| id | 타입 | 설명 |
| --- | --- | --- |
| `#email` | input[type=email] | 이메일 입력 |
| `#password` | input[type=password] | 비밀번호 입력 |
| `#login-btn` | button | 로그인 버튼 (텍스트 "로그인") |
| `#error-message` | div[role=alert] | 에러 메시지 영역 |
| `#user-greeting` | span | 로그인 후 사용자 이름 표시 |
| `#otp` | input | Admin 의 OTP 입력 |
| `#otp-submit` | button | OTP 제출 버튼 (텍스트 "확인") |

## 보안 정책

- 비밀번호 최소 12자. 영문 대소문자 + 숫자 + 특수문자 각 1개 이상.
- HTTPS 필수. HTTP 로 접근 시 `/login` 으로 자동 redirect.
- session token 은 HttpOnly / Secure cookie. JS 접근 불가.
