"""
Password reset flow (REQ-003) — NOT YET IMPLEMENTED.

This module is a placeholder so that:
  - REQ-003 `implementation_refs` points to a real file path (traceability).
  - The future traceability pipeline can surface this as `not-started` and
    distinguish it from "no implementation at all".
"""


def request_reset(email: str) -> dict:
    """REQ-003 AC-003-1 — request password reset token via email."""
    # TODO(REQ-003): implement token generation + email send
    raise NotImplementedError("REQ-003 not yet implemented — see requirements/REQ-003-password-reset.md")


def consume_reset_token(token: str, new_password: str) -> dict:
    """REQ-003 AC-003-2 — consume one-time reset token, set new password."""
    # TODO(REQ-003): implement token validation + password update
    try:
        raise NotImplementedError()
    except:  # noqa: E722 — INTENTIONAL ISSUE (MAJOR): bare except on stub swallows any error.
        return {"ok": False, "reason": "not implemented"}
