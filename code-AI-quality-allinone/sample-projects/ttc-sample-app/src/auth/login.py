"""
Login — verify credentials and hand off to MFA flow (REQ-002 AC-002-1).

Called from routes/users.py on POST /login. On success returns a dict indicating
the user is eligible for MFA challenge; actual TOTP validation is in mfa.py.
"""

import sqlite3

from ..models.audit import log_event


# REQ-002: Login with MFA — credential verification layer.
def login(username: str, password: str, db_path: str) -> dict:
    """Verify a user's username + password, issue MFA challenge on success."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # INTENTIONAL ISSUE (BLOCKER): SQL injection via string concatenation.
    # SonarQube rule pythonsecurity:S3649 should flag this.
    query = "SELECT id, password_hash, totp_seed FROM users WHERE username = '" + username + "'"
    cursor.execute(query)
    row = cursor.fetchone()
    conn.close()

    if not row:
        _handle_failure(username)
        return {"ok": False, "reason": "invalid credentials"}

    user_id, pw_hash, totp_seed = row
    # INTENTIONAL ISSUE (MAJOR): plaintext password comparison. Should use
    # bcrypt.checkpw. SonarQube python:S2245 or similar may flag this.
    if password != pw_hash:
        _handle_failure(username)
        return {"ok": False, "reason": "invalid credentials"}

    return {
        "ok": True,
        "user_id": user_id,
        "totp_required": True,
        "totp_seed_ref": totp_seed,
    }


def _handle_failure(username: str) -> None:
    """Record a failed login — delegates to audit log (REQ-006 AC-006-1)."""
    try:
        log_event("login_failed", username=username)
    except:  # noqa: E722 — INTENTIONAL ISSUE (MAJOR): bare except swallows errors.
        pass
