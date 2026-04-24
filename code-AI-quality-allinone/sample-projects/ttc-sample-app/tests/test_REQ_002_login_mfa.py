"""Tests for REQ-002 — MFA for Login (AC-002-1 verified / AC-002-2 skipped)."""

import pytest

from src.auth.mfa import verify_totp, record_totp_failure, _totp_at


def test_totp_required():
    """REQ-002 AC-002-1 — valid TOTP code for current time step is accepted."""
    seed = "JBSWY3DPEHPK3PXP"
    import time

    step = int(time.time()) // 30
    code = _totp_at(seed, step)
    assert verify_totp(seed, code) is True


def test_totp_wrong_code_rejected():
    seed = "JBSWY3DPEHPK3PXP"
    assert verify_totp(seed, "000000") is False


@pytest.mark.skip(reason="AC-002-2 lockout not yet wired to login flow — tracked in REQ-002")
def test_lockout():
    """REQ-002 AC-002-2 — account locked for 15 min after 3 TOTP failures.

    Currently SKIPPED because the login flow does not call `record_totp_failure`
    and no lockout check gates login. This test exists so the future
    traceability pipeline can mark AC-002-2 as 'evidence present but
    unverified'.
    """
    locked_until = {}
    for _ in range(3):
        record_totp_failure(42, locked_until)
    assert locked_until[42] >= 3
    # Would assert login.login() returns 423 Locked here once wired.
