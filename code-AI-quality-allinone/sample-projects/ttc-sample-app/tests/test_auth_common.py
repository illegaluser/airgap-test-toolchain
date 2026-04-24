"""Tests for REQ-006 (audit log) and REQ-007 (rate limit) cross-cutting."""

import sqlite3

import pytest

from src.models.audit import log_event
from src.utils.rate_limiter import allow_request, reset


@pytest.fixture
def audit_db(tmp_path):
    path = tmp_path / "audit.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE audit_events ("
        " id INTEGER PRIMARY KEY, ts INTEGER, event_type TEXT, payload TEXT)"
    )
    conn.commit()
    conn.close()
    return str(path)


def test_failed_login_audited(audit_db):
    """REQ-006 AC-006-1 — failed login produces an audit row with username."""
    log_event("login_failed", db_path=audit_db, username="alice", ip="10.0.0.1")
    conn = sqlite3.connect(audit_db)
    rows = conn.execute("SELECT event_type, payload FROM audit_events").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "login_failed"
    assert "username=alice" in rows[0][1]


def test_role_change_audited(audit_db):
    """REQ-006 AC-006-2 — role change produces audit row with old/new roles."""
    log_event(
        "role_changed",
        db_path=audit_db,
        actor_id=1,
        target_id=42,
        old_role="viewer",
        new_role="editor",
    )
    conn = sqlite3.connect(audit_db)
    rows = conn.execute("SELECT payload FROM audit_events").fetchall()
    conn.close()
    payload = rows[0][0]
    assert "old_role=viewer" in payload
    assert "new_role=editor" in payload


def test_rate_limit_login():
    """REQ-007 AC-007-1 — 10 req/min on /login; the 11th in a row is rejected."""
    reset()
    key = "login:test-ip"
    for _ in range(10):
        assert allow_request(key, limit=10, window_seconds=60) is True
    assert allow_request(key, limit=10, window_seconds=60) is False
