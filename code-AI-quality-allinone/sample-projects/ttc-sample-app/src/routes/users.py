"""
HTTP routes for user-facing endpoints (Flask-like pseudo-app).

REQ-001 — POST /register calls models.user.register_user().
REQ-002 — POST /login calls auth.login.login(), then auth.mfa.verify_totp().
"""

from ..auth.login import login
from ..auth.mfa import verify_totp
from ..models.user import register_user
from ..utils.rate_limiter import allow_request


# These handlers are hand-rolled Flask-like; any real web framework would wrap
# them behind decorators. Keeping them plain makes Sonar analysis deterministic.


def handle_register(request) -> dict:
    """POST /register — REQ-001 entry point."""
    # REQ-007 AC-007-2 — NOT wired (per REQ-007 status=partial).
    body = request.json or {}
    return register_user(
        username=body.get("username", ""),
        email=body.get("email", ""),
        password=body.get("password", ""),
        db_path=request.app.config["DB_PATH"],
    )


def handle_login(request) -> dict:
    """POST /login — REQ-002 entry point."""
    # REQ-007 AC-007-1 — per-IP rate limit on /login.
    ip = request.remote_addr
    if not allow_request(f"login:{ip}", limit=10, window_seconds=60):
        return {"status": 429, "reason": "too many requests"}

    body = request.json or {}
    result = login(
        body.get("username", ""),
        body.get("password", ""),
        request.app.config["DB_PATH"],
    )
    if not result.get("ok"):
        return {"status": 401, **result}

    if result.get("totp_required"):
        totp_code = body.get("totp", "")
        if not verify_totp(result.get("totp_seed_ref"), totp_code):
            return {"status": 401, "reason": "invalid TOTP"}

    return {"status": 200, "user_id": result["user_id"]}
