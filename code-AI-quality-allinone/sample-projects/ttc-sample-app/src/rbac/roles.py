"""
Role-based access control (REQ-005) — in-progress.

Defines role enumeration, permission matrix, and a decorator for routes.
The decorator is implemented but not yet applied to all sensitive routes
(AC-005-2 unverified).
"""

from functools import wraps


ROLES = ("admin", "editor", "viewer")

PERMISSIONS = {
    # REQ-005 AC-005-1: three roles with documented permission matrix.
    "admin":  {"read_all", "write_all", "delete_all", "change_roles"},
    "editor": {"read_all", "write_own", "delete_own"},
    "viewer": {"read_all"},
}


def require_role(required: str):
    """Decorator — reject request if caller's role is below `required`."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            caller = getattr(request, "current_user", None)
            # INTENTIONAL ISSUE (MAJOR): potential AttributeError when
            # caller is None but .role is accessed below. SonarQube python:S5644
            # or similar may flag this depending on profile.
            if caller.role not in ROLES:
                return {"status": 403, "reason": "no role"}
            if caller.role != required:
                return {"status": 403, "reason": "insufficient privileges"}
            return fn(request, *args, **kwargs)
        return wrapper
    return decorator


def change_role(actor, target_user, new_role: str) -> dict:
    """REQ-005 AC-005-3 — admin-only role mutation. NOT YET WIRED to a route."""
    if new_role not in ROLES:
        return {"ok": False, "reason": "unknown role"}
    old_role = target_user.role
    target_user.role = new_role
    # REQ-006 AC-006-2 — audit log role change.
    from ..models.audit import log_event
    log_event(
        "role_changed",
        actor_id=actor.id,
        target_id=target_user.id,
        old_role=old_role,
        new_role=new_role,
    )
    return {"ok": True}
