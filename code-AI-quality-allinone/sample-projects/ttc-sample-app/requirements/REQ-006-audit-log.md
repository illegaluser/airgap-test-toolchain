---
id: REQ-006
title: Audit Log for Security Events
priority: high
status: done
owner: platform-team
acceptance_criteria:
  - id: AC-006-1
    desc: Failed login attempts logged with username + timestamp + source IP
    evidence_test: tests/test_auth_common.py::test_failed_login_audited
    verified: true
  - id: AC-006-2
    desc: Role changes logged with actor / target / old_role / new_role
    evidence_test: tests/test_auth_common.py::test_role_change_audited
    verified: true
implementation_refs:
  - src/models/audit.py
---

# REQ-006 — Audit Log for Security Events

## Background

Compliance frameworks require tamper-resistant audit trail for authentication
and privilege changes.

## Scope

- Append-only `audit_events` table.
- At minimum logs: failed logins, successful admin actions, role changes,
  password resets.
- Logs are retained 1 year (policy; not implemented yet as a cron).

## Technical Notes

`src/models/audit.py::log_event(event_type, **fields)` — all security-sensitive
call sites invoke this. The function itself uses `INSERT INTO audit_events`
(parameterized).

## Implementation Status

**done** — 2/2 AC verified. Retention cron still pending (not in this REQ's
scope but tracked informally).
