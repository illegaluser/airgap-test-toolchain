"""
Session middleware — enforces idle and absolute timeout (REQ-004).

Every authenticated request calls `check_session(sid)` which either extends the
session (idle reset) or invalidates it if either timeout has elapsed.
"""

import time


# REQ-004 AC-004-1 / AC-004-2
IDLE_TIMEOUT_SECONDS = 30 * 60    # 30 min
ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60  # 8 hours


def check_session(session_id: str, sessions: dict) -> dict:
    """Validate session and extend last_activity_at. Returns new state dict."""
    sess = sessions.get(session_id)
    if not sess:
        return {"valid": False, "reason": "unknown session"}

    now = time.time()
    if now - sess["last_activity_at"] > IDLE_TIMEOUT_SECONDS:
        sessions.pop(session_id, None)
        return {"valid": False, "reason": "idle timeout"}
    if now - sess["created_at"] > ABSOLUTE_TIMEOUT_SECONDS:
        sessions.pop(session_id, None)
        return {"valid": False, "reason": "absolute timeout"}

    sess["last_activity_at"] = now
    return {"valid": True, "user_id": sess["user_id"]}


def issue_cookie(response, session_id: str) -> None:
    """Attach session cookie to response.

    INTENTIONAL ISSUE (CRITICAL): `secure=False` lets the cookie travel over
    plain HTTP. Production must set `secure=True`. SonarQube
    pythonsecurity:S2092 (cookie without secure flag).
    """
    response.set_cookie(
        "sid",
        session_id,
        httponly=True,
        secure=False,  # ← BAD: must be True in production.
        samesite="Lax",
        max_age=ABSOLUTE_TIMEOUT_SECONDS,
    )
