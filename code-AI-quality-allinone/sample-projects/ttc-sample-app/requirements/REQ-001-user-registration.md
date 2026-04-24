---
id: REQ-001
title: User Registration
priority: high
status: done
owner: auth-team
acceptance_criteria:
  - id: AC-001-1
    desc: Accept username + email + password via POST /register
    evidence_test: tests/test_REQ_001_user_registration.py::test_register_success
    verified: true
  - id: AC-001-2
    desc: Reject duplicate usernames with HTTP 409
    evidence_test: tests/test_REQ_001_user_registration.py::test_duplicate_username
    verified: true
  - id: AC-001-3
    desc: Hash password before persisting (never store plaintext)
    evidence_test: tests/test_REQ_001_user_registration.py::test_password_hashed
    verified: true
implementation_refs:
  - src/routes/users.py
  - src/models/user.py
---

# REQ-001 — User Registration

## Background

First-touch onboarding — users must be able to create an account.

## Scope

- Username + email + password input via `POST /register`.
- Password hashed with `bcrypt` (min 12 rounds).
- Duplicate detection on username (email dup warning only).

## Out of Scope

- Email verification flow (REQ-003 covers password reset tokens; email confirm
  is a separate requirement — not yet filed).
- Social login (OAuth / SAML).

## Technical Notes

Database: SQLite (development) / PostgreSQL (production). User table schema
owned by `src/models/user.py`. Registration endpoint in `src/routes/users.py`.

## Implementation Status

**done** — 3/3 acceptance criteria verified.
