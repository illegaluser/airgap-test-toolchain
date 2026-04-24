---
id: REQ-003
title: Password Reset via Email Token
priority: high
status: not-started
owner: auth-team
acceptance_criteria:
  - id: AC-003-1
    desc: User requests reset via POST /reset with email
    evidence_test: null
    verified: false
  - id: AC-003-2
    desc: Server issues time-limited token (30 min) and emails reset link
    evidence_test: null
    verified: false
implementation_refs:
  - src/auth/reset.py
---

# REQ-003 — Password Reset via Email Token

## Background

Users who forget passwords must be able to reset them without contacting
support.

## Scope

- `POST /reset` accepts an email address.
- Server generates a cryptographically random 32-byte token, stores
  `(token_hash, user_id, expires_at)` in `password_reset_tokens` table.
- Email containing `https://app.example.com/reset/<token>` is sent via SMTP.
- `POST /reset/<token>` with new password consumes the token (one-time use).

## Out of Scope

- Security questions fallback.
- SMS-based reset.

## Technical Notes

**Not implemented** — `src/auth/reset.py` currently has a stub raising
`NotImplementedError`. This exists as a placeholder to capture REQ traceability
and to show the "not-started" state in the coverage matrix.

## Implementation Status

**not-started** — 0/2 acceptance criteria verified.
