"""Tests for REQ-004 — Session Timeout (AC-004-1 / AC-004-2)."""

import time

from src.auth.session import (
    check_session,
    IDLE_TIMEOUT_SECONDS,
    ABSOLUTE_TIMEOUT_SECONDS,
)


def _make_session(now, last_activity_at, created_at, user_id=1):
    return {
        "user_id": user_id,
        "last_activity_at": last_activity_at,
        "created_at": created_at,
    }


def test_idle_timeout():
    """REQ-004 AC-004-1 — idle > 30 min invalidates session."""
    now = time.time()
    sessions = {"sid1": _make_session(now, now - IDLE_TIMEOUT_SECONDS - 1, now - 60)}
    result = check_session("sid1", sessions)
    assert result["valid"] is False
    assert result["reason"] == "idle timeout"
    assert "sid1" not in sessions


def test_absolute_timeout():
    """REQ-004 AC-004-2 — >8h lifetime invalidates session regardless of activity."""
    now = time.time()
    # Recently active (well within idle window) but created >8h ago.
    sessions = {
        "sid2": _make_session(now, now - 10, now - ABSOLUTE_TIMEOUT_SECONDS - 1)
    }
    result = check_session("sid2", sessions)
    assert result["valid"] is False
    assert result["reason"] == "absolute timeout"


def test_active_session_extended():
    """Sanity — active session returns valid + updates last_activity_at."""
    now = time.time()
    sessions = {"sid3": _make_session(now, now - 5, now - 60)}
    before = sessions["sid3"]["last_activity_at"]
    result = check_session("sid3", sessions)
    assert result["valid"] is True
    assert sessions["sid3"]["last_activity_at"] >= before
