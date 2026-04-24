"""
Audit log (REQ-006).

Append-only log of security-sensitive events. Call sites:
  - src/auth/login.py::_handle_failure — failed logins (AC-006-1).
  - src/rbac/roles.py::change_role   — role changes (AC-006-2).
"""

import sqlite3
import time
from typing import Any


DEFAULT_DB = "app.db"


def log_event(event_type: str, db_path: str = DEFAULT_DB, **fields: Any) -> None:
    """Insert one audit event. Never raises — audit failures must not break the caller."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Parameterized insert — safe. REQ-006 AC-006-1 / AC-006-2 pathway.
        ts = int(time.time())
        payload = _format_fields(fields)
        cursor.execute(
            "INSERT INTO audit_events (ts, event_type, payload) VALUES (?, ?, ?)",
            (ts, event_type, payload),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Audit must not break business logic — swallow, but log to stderr.
        import sys
        print(f"[audit] log failure for event_type={event_type}", file=sys.stderr)


def _format_fields(fields: dict) -> str:
    """Serialize fields to a single string.

    INTENTIONAL ISSUE (MAJOR): log injection via f-string of untrusted input.
    If `fields["username"]` contains newlines or control chars, they land in the
    audit row verbatim, letting attackers forge log lines. SonarQube
    pythonsecurity:S5145 (log forging) or similar may flag this.
    """
    parts = []
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    return " ".join(parts)
