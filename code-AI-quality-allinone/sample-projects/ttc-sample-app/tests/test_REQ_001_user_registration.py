"""Tests for REQ-001 — User Registration (AC-001-1 / AC-001-2 / AC-001-3)."""

import os
import sqlite3
import tempfile

import pytest

from src.models.user import register_user, check_password


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE users ("
        " id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT,"
        " password_hash TEXT, role TEXT DEFAULT 'viewer')"
    )
    conn.commit()
    conn.close()
    return str(path)


def test_register_success(db_path):
    """REQ-001 AC-001-1 — basic happy path."""
    result = register_user("alice", "alice@example.com", "s3cret!", db_path)
    assert result["ok"] is True
    assert result["user_id"] > 0


def test_duplicate_username(db_path):
    """REQ-001 AC-001-2 — second register with same username returns 409."""
    register_user("bob", "bob@example.com", "pw1", db_path)
    result = register_user("bob", "bob2@example.com", "pw2", db_path)
    assert result["ok"] is False
    assert result["status"] == 409


def test_password_hashed(db_path):
    """REQ-001 AC-001-3 — plaintext password never stored."""
    register_user("carol", "carol@example.com", "plaintext", db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT password_hash FROM users WHERE username = 'carol'").fetchone()
    conn.close()
    assert row is not None
    stored_hash = row[0]
    assert stored_hash != "plaintext"
    assert check_password("plaintext", stored_hash) is True
    assert check_password("wrong", stored_hash) is False
