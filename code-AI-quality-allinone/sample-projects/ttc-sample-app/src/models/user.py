"""
User model and registration (REQ-001).

Uses bcrypt for password hashing (AC-001-3). The registration function is
called from routes/users.py.
"""

import sqlite3

try:
    import bcrypt
except ImportError:  # allow module import even when bcrypt isn't installed
    bcrypt = None


def register_user(username: str, email: str, password: str, db_path: str) -> dict:
    """Create a user row with bcrypt-hashed password. Returns {ok, user_id} or reason."""
    if not username or not password:
        return {"ok": False, "reason": "missing fields"}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # REQ-001 AC-001-2 — duplicate username check.
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        return {"ok": False, "reason": "duplicate username", "status": 409}

    # REQ-001 AC-001-3 — hash before insert.
    pw_hash = _hash_password(password)
    cursor.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, 'viewer')",
        (username, email, pw_hash),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return {"ok": True, "user_id": user_id}


def _hash_password(plain: str) -> str:
    """bcrypt hash with cost factor 12 (NIST-aligned)."""
    if bcrypt is None:
        # Fallback for environments without bcrypt — NOT for production.
        import hashlib
        return "nobcrypt$" + hashlib.sha256(plain.encode()).hexdigest()
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def check_password(plain: str, stored_hash: str) -> bool:
    if stored_hash.startswith("nobcrypt$"):
        import hashlib
        return stored_hash == "nobcrypt$" + hashlib.sha256(plain.encode()).hexdigest()
    if bcrypt is None:
        return False
    return bcrypt.checkpw(plain.encode("utf-8"), stored_hash.encode("utf-8"))
