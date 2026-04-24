---
id: REQ-007
title: API Rate Limiting
priority: medium
status: partial
owner: platform-team
acceptance_criteria:
  - id: AC-007-1
    desc: Limit /login to 10 req/min per IP; excess returns HTTP 429
    evidence_test: tests/test_auth_common.py::test_rate_limit_login
    verified: true
  - id: AC-007-2
    desc: Limit /register to 3 req/hour per IP
    evidence_test: null
    verified: false
implementation_refs:
  - src/utils/rate_limiter.py
---

# REQ-007 — API Rate Limiting

## Background

Unthrottled endpoints invite credential stuffing and enumeration attacks.

## Scope

- In-process counter keyed by `(client_ip, route)`.
- Sliding window: last 60 s for login, last 3600 s for register.
- On threshold breach return `429 Too Many Requests`.
- NOT distributed — single-instance only (future: Redis-backed).

## Technical Notes

`src/utils/rate_limiter.py` maintains a dict of deque timestamps per key.
The current implementation is **not thread-safe** — a race condition allows
slightly more requests than the limit under concurrent load. SonarQube is
expected to flag this (MAJOR).

## Implementation Status

**partial** — /login limit works (AC-007-1), /register limit not yet wired
(AC-007-2).
