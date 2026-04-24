# Architecture — ttc-sample-app

Minimal Flask-style user management app used as **dual-track sample** for
the airgap-test-toolchain:

- Track 1 (코드품질) — Sonar 가 발견할 다양한 결함을 의도적으로 심어 LLM
  분석 파이프라인 (01~04) 에 풍부한 입력을 제공.
- Track 2 (요구사항 구현율) — `requirements/` 의 REQ-NNN-*.md 에 front-matter
  metadata 를 박아 미래 traceability 파이프라인이 결정적으로 파싱할 수 있게 함.

## 레이어

```
┌────────────────────────────────────────────────┐
│ frontend/                                       │
│   login.js  → POST /login                       │
│   session.js → GET /api/ping (heartbeat)        │
└───────────────────────┬─────────────────────────┘
                        │ HTTP
┌───────────────────────▼─────────────────────────┐
│ src/routes/                                      │
│   users.py   — /register, /login                 │
│   api.py     — /go (redirect), /api/*            │
├──────────────────────────────────────────────────┤
│ src/auth/                                        │
│   login.py   — 자격증명 검증 (REQ-002)           │
│   mfa.py     — TOTP 검증 (REQ-002)               │
│   reset.py   — stub (REQ-003 not-started)        │
│   session.py — 세션 만료 (REQ-004)               │
│ src/rbac/roles.py — RBAC decorator (REQ-005)    │
│ src/utils/rate_limiter.py — 429 guard (REQ-007) │
├──────────────────────────────────────────────────┤
│ src/models/                                      │
│   user.py  — bcrypt 해시, 등록 (REQ-001)         │
│   audit.py — 이벤트 로깅 (REQ-006)               │
└──────────────────────────────────────────────────┘
                        │ SQLite
                    ( app.db )
```

## 데이터 모델

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE,
  email TEXT,
  password_hash TEXT,
  totp_seed TEXT,
  role TEXT DEFAULT 'viewer',
  locked_until INTEGER DEFAULT 0
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER,
  created_at INTEGER,
  last_activity_at INTEGER
);

CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY,
  ts INTEGER,
  event_type TEXT,
  payload TEXT
);

CREATE TABLE password_reset_tokens (  -- REQ-003 future
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER,
  expires_at INTEGER
);
```

## REQ ↔ 구현 ↔ 테스트 매핑

| REQ     | 구현 파일                                            | 테스트 파일                                   | 상태           |
| ------- | --------------------------------------------------- | -------------------------------------------- | -------------- |
| REQ-001 | src/routes/users.py, src/models/user.py             | tests/test_REQ_001_user_registration.py     | done           |
| REQ-002 | src/auth/login.py, src/auth/mfa.py, frontend/login.js | tests/test_REQ_002_login_mfa.py            | partial        |
| REQ-003 | src/auth/reset.py (stub)                            | (none)                                       | not-started    |
| REQ-004 | src/auth/session.py, frontend/session.js            | tests/test_REQ_004_session_timeout.py       | done           |
| REQ-005 | src/rbac/roles.py                                   | (none — AC-005-1 doc만, 나머지 unverified)   | in-progress    |
| REQ-006 | src/models/audit.py                                 | tests/test_auth_common.py                   | done           |
| REQ-007 | src/utils/rate_limiter.py                           | tests/test_auth_common.py (AC-007-1 only)   | partial        |

이 매핑이 front-matter 의 `implementation_refs` / `evidence_test` 와 일치하도록
유지. 미래 `06-요구사항-구현율` Jenkins Job 이 이 일치성을 자동 검증.

## 비-범위

- 실제 서비스 배포 (Dockerfile 별도 없음).
- 프로덕션 수준 보안 (의도적으로 결함 있음).
- 완전한 Flask 앱 엔트리 (route 매핑이 아닌 handler 함수만).
