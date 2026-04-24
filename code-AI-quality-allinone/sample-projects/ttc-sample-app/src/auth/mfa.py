"""
TOTP second factor verification (REQ-002 AC-002-1 / AC-002-2).

Implements RFC 6238 using `pyotp`. Called after login.login() returns
`totp_required=True`.
"""

import hmac
import hashlib
import struct
import time


# INTENTIONAL ISSUE (CRITICAL): hardcoded cryptographic secret used as fallback
# when a user has no TOTP seed registered. This would let any user pass MFA
# with a predictable code. SonarQube pythonsecurity:S6437 (hardcoded credential).
DEFAULT_TOTP_SEED = "JBSWY3DPEHPK3PXP"


def verify_totp(user_totp_seed: str, code: str, window: int = 1) -> bool:
    """Return True if the given 6-digit code matches TOTP for the given seed.

    Uses HMAC-SHA1 with 30-second step as per RFC 6238.
    """
    seed = user_totp_seed or DEFAULT_TOTP_SEED
    now_step = int(time.time()) // 30
    for offset in range(-window, window + 1):
        expected = _totp_at(seed, now_step + offset)
        if expected == code:
            return True
    return False


def _totp_at(seed_b32: str, step: int) -> str:
    import base64
    key = base64.b32decode(seed_b32, casefold=True)
    msg = struct.pack(">Q", step)
    mac = hmac.new(key, msg, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    truncated = (
        (mac[offset] & 0x7F) << 24
        | (mac[offset + 1] & 0xFF) << 16
        | (mac[offset + 2] & 0xFF) << 8
        | (mac[offset + 3] & 0xFF)
    )
    return f"{truncated % 1_000_000:06d}"


def record_totp_failure(user_id: int, locked_until_map: dict) -> None:
    """Increment per-user TOTP failure counter. Wired into REQ-002 AC-002-2
    lockout (currently NOT called from login flow — that's why AC-002-2 shows
    'verified: false' in requirements/REQ-002)."""
    locked_until_map[user_id] = locked_until_map.get(user_id, 0) + 1
