---
id: REQ-002
title: Multi-Factor Authentication for Login
priority: critical
status: partial
owner: auth-team
acceptance_criteria:
  - id: AC-002-1
    desc: Require TOTP code after username/password verification
    evidence_test: tests/test_REQ_002_login_mfa.py::test_totp_required
    verified: true
  - id: AC-002-2
    desc: Lock account for 15 minutes after 3 failed TOTP attempts
    evidence_test: tests/test_REQ_002_login_mfa.py::test_lockout
    verified: false
  - id: AC-002-3
    desc: Allow user to mark a device as trusted for 30 days
    evidence_test: null
    verified: false
implementation_refs:
  - src/auth/login.py
  - src/auth/mfa.py
  - frontend/login.js
---

# REQ-002 — Multi-Factor Authentication for Login

## Background

SOC2 / ISO 27001 compliance requires multi-factor authentication (MFA) for all
authenticated user logins. This requirement covers the baseline TOTP flow.

## Scope

- Time-based One-Time Password (RFC 6238) as second factor.
- Username + password first → server issues MFA challenge → client posts TOTP.
- Rate-limit TOTP attempts to mitigate brute force.
- Trusted device option to reduce friction on repeat logins.

## Out of Scope

- SMS / push-based second factors (future requirement).
- WebAuthn / passkey support (REQ to be filed).

## Technical Notes

Server-side TOTP seed stored per-user in `users.totp_seed`. Verification uses
`pyotp`. Account lock state tracked on `users.locked_until` timestamp.

## Implementation Status

**partial** — basic TOTP challenge implemented (`AC-002-1`). Lockout exists in
code but not wired to login flow (`AC-002-2` test present but skipped). Trusted
device flow not started (`AC-002-3`).
