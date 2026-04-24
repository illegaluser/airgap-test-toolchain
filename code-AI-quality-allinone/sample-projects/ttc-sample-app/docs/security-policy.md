# Security Policy — ttc-sample-app

> 이 문서는 **RBAC 권한 매트릭스** (REQ-005 AC-005-1 증거물) 와 보안 기준선을
> 명시합니다. 실제 프로덕션 앱이 아니므로 **의도적으로 결함을 남겨 둔 부분**
> (아래 "Known Defects") 은 수정하지 말 것 — Sonar 분석 + LLM 파이프라인이
> 찾아내야 하는 대상입니다.

## Role Permission Matrix (REQ-005 AC-005-1)

| Permission        | admin | editor | viewer |
| ----------------- | :---: | :----: | :----: |
| `read_all`        | ✓    | ✓      | ✓      |
| `write_own`       | ✓    | ✓      |        |
| `write_all`       | ✓    |        |        |
| `delete_own`      | ✓    | ✓      |        |
| `delete_all`      | ✓    |        |        |
| `change_roles`    | ✓    |        |        |
| `view_audit_log`  | ✓    |        |        |

- `admin` 은 모든 권한. 단, 자기 자신의 role 은 변경 불가 (lockout 방지).
- `editor` 는 자기가 생성한 리소스에 한해 write/delete 가능.
- `viewer` 는 read-only.

소스 위치: `src/rbac/roles.py::PERMISSIONS` dict 와 **반드시 일치해야 함**.

## Password Policy (REQ-001)

- 최소 길이 8자, 영숫자 혼합.
- bcrypt cost 12 (NIST SP 800-63B 최소권장).
- history 3: 최근 3 개 해시와 동일한 비번으로 재설정 불가 — REQ-003 확장 예정.

## MFA Policy (REQ-002)

- 모든 user 계정에 TOTP 필수. 초기 등록 시 QR code + 복구 코드 10개 생성
  (복구 코드 발급은 REQ-002 확장 항목, 미구현).
- TOTP failure 3회 시 계정 15분 lock (AC-002-2 — 현재 test skip).
- 디바이스 trust 기간 30일 (AC-002-3 — not started).

## Session Policy (REQ-004)

- Idle timeout 30 min, absolute timeout 8 h. 양쪽 모두 서버 측 enforcement.
- 쿠키 flags: `HttpOnly`, `SameSite=Lax`, `Secure` **(프로덕션)**.
  — 현재 dev 설정에서는 `Secure=False` (Known Defect 참고).

## Rate Limiting (REQ-007)

- `/login`: 10 req/min per IP.
- `/register`: 3 req/hour per IP (**미구현** — AC-007-2).

## Audit Log (REQ-006)

모든 보안 이벤트는 `audit_events` 테이블에 append-only 로 기록:

- `login_failed` — username, ip, ts
- `login_success` — user_id, ip, ts
- `role_changed` — actor_id, target_id, old_role, new_role
- `password_reset_requested` — user_id, ip (REQ-003 확장 항목)

보유 기간 1년 — 정리 cron 은 별도 운영 작업 (본 앱 범위 밖).

## Known Defects (의도적 — 이번 샘플의 핵심 기능)

Sonar + LLM 파이프라인이 찾아내야 하는 **의도된** 결함들. 이 리스트는 LLM 이
산출한 이슈와 "정답지" 비교에도 쓸 수 있다 (eval_rag_quality.py 의 golden CSV
근거 자료).

| 파일                                | 결함                                                  | 기대 Severity |
| ---------------------------------- | ---------------------------------------------------- | ------------- |
| `src/auth/login.py`                | SQL injection (string concat in query)                | BLOCKER       |
| `src/auth/login.py`                | plaintext password comparison                         | MAJOR         |
| `src/auth/login.py`                | bare except swallowing all errors in `_handle_failure` | MAJOR         |
| `src/auth/mfa.py`                  | hardcoded TOTP seed fallback                          | CRITICAL      |
| `src/auth/reset.py`                | bare except on stub `consume_reset_token`             | MAJOR         |
| `src/auth/session.py`              | cookie `Secure=False`                                 | CRITICAL      |
| `src/models/audit.py`              | log injection via f-string                            | MAJOR         |
| `src/rbac/roles.py`                | potential None attribute access on `caller.role`      | MAJOR         |
| `src/routes/api.py::handle_redirect` | open redirect (no allowlist)                        | CRITICAL      |
| `src/routes/api.py::cors_headers`  | wildcard Origin + credentials                         | MAJOR         |
| `src/utils/rate_limiter.py`        | race condition on shared deque                        | MAJOR         |
| `frontend/login.js`                | XSS via innerHTML from server string                  | BLOCKER       |
| `frontend/session.js`              | auth token in localStorage                            | MAJOR         |
