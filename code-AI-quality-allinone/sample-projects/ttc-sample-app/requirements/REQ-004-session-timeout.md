---
id: REQ-004
title: Session Timeout
priority: medium
status: done
owner: auth-team
acceptance_criteria:
  - id: AC-004-1
    desc: Idle timeout forces re-login after 30 minutes of inactivity
    evidence_test: tests/test_REQ_004_session_timeout.py::test_idle_timeout
    verified: true
  - id: AC-004-2
    desc: Absolute timeout forces re-login after 8 hours regardless of activity
    evidence_test: tests/test_REQ_004_session_timeout.py::test_absolute_timeout
    verified: true
implementation_refs:
  - src/auth/session.py
  - frontend/session.js
---

# REQ-004 — Session Timeout

## Background

NIST SP 800-63B recommends idle and absolute session timeouts to limit the
blast radius of stolen session cookies.

## Scope

- Idle timeout: 30 min — renewed on any authenticated request.
- Absolute timeout: 8 h — non-renewable, forces re-login regardless of activity.
- Timeouts enforced server-side; client JS sends a heartbeat + shows a
  "session expiring" banner.

## Technical Notes

Server tracks `sessions.last_activity_at` and `sessions.created_at`. Expiry
checked on each request in `src/auth/session.py::check_session`.

## Implementation Status

**done** — 2/2 AC verified.
