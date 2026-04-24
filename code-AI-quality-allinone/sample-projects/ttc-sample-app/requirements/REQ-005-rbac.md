---
id: REQ-005
title: Role-Based Access Control (RBAC)
priority: high
status: in-progress
owner: platform-team
acceptance_criteria:
  - id: AC-005-1
    desc: Three roles — admin / editor / viewer — with documented permission matrix
    evidence_test: null
    verified: true
  - id: AC-005-2
    desc: Route-level decorator enforces required role (returns 403 on mismatch)
    evidence_test: null
    verified: false
  - id: AC-005-3
    desc: Admin can change user roles via POST /admin/users/<id>/role
    evidence_test: null
    verified: false
implementation_refs:
  - src/rbac/roles.py
---

# REQ-005 — Role-Based Access Control

## Background

Separation of duties — not all users should be able to delete records or
change others' roles.

## Scope

- Role model with three built-in roles: `admin`, `editor`, `viewer`.
- Permission matrix documented in `docs/security-policy.md`.
- `@require_role("admin")` decorator on sensitive routes.
- Role mutation UI + API — admin-only.

## Technical Notes

Role stored on `users.role` column (enum). Decorator inspects
`request.current_user.role` populated by session middleware.

## Implementation Status

**in-progress** — role matrix defined (AC-005-1), decorator code exists but
is not applied to all sensitive routes (AC-005-2 unverified), role mutation
endpoint not yet implemented (AC-005-3).
