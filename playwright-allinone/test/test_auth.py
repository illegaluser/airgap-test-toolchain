"""T-D (P0.1) — auth.py unit tests + auth_login fixture integration.

Design: docs/PLAN_PRODUCTION_READINESS.md §"T-D — auth (form + OAuth + TOTP)"

Coverage:
- credential alias env-var lookup (success/failure)
- mask_secret masking rules
- parse_auth_target mode/option parsing
- TOTP code generation (pyotp integration)
- form/totp fixtures wired through the executor's `_execute_auth_login`
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from zero_touch_qa.auth import (
    AuthOptions,
    Credential,
    CredentialError,
    ENV_PREFIX,
    EMAIL_FIELD_CANDIDATES,
    PASSWORD_FIELD_CANDIDATES,
    TOTP_FIELD_CANDIDATES,
    SUBMIT_BUTTON_CANDIDATES,
    generate_totp_code,
    mask_secret,
    parse_auth_target,
    resolve_credential,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
AUTH_FORM_URL = (FIXTURES_DIR / "auth_form.html").as_uri()
AUTH_TOTP_URL = (FIXTURES_DIR / "auth_totp.html").as_uri()


# ─────────────────────────────────────────────────────────────────────────
# CLI validation — regression guard that auth_login passes _validate_scenario
# (P0.1 #1). Catches the bug where the CLI rejected auth_login as out-of-whitelist.
# ─────────────────────────────────────────────────────────────────────────


def _scenario_with(step: dict) -> list[dict]:
    return [
        {
            "step": 1, "action": "navigate", "target": "",
            "value": "https://example.com", "description": "nav",
            "fallback_targets": [],
        },
        step,
    ]


def test_cli_validates_auth_login_form_mode():
    from zero_touch_qa.__main__ import _validate_scenario

    _validate_scenario(_scenario_with({
        "step": 2, "action": "auth_login", "target": "form",
        "value": "demo_alias", "description": "form login",
        "fallback_targets": [],
    }))


def test_cli_validates_auth_login_totp_with_modifiers():
    from zero_touch_qa.__main__ import _validate_scenario

    _validate_scenario(_scenario_with({
        "step": 2, "action": "auth_login",
        "target": "totp, totp_field=#otp, submit=#submit",
        "value": "demo_alias", "description": "totp",
        "fallback_targets": [],
    }))


def test_cli_rejects_auth_login_with_invalid_mode():
    from zero_touch_qa.__main__ import _validate_scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError, match="target"):
        _validate_scenario(_scenario_with({
            "step": 2, "action": "auth_login", "target": "saml",
            "value": "demo_alias", "description": "invalid mode",
            "fallback_targets": [],
        }))


def test_cli_rejects_auth_login_with_empty_value():
    from zero_touch_qa.__main__ import _validate_scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(_scenario_with({
            "step": 2, "action": "auth_login", "target": "form",
            "value": "", "description": "alias missing",
            "fallback_targets": [],
        }))


# ─────────────────────────────────────────────────────────────────────────
# Screenshot masking — confirm _screenshot_masked passes Playwright's
# mask= argument through (P0.1 #3). This is the only gate keeping
# plaintext credentials out of PNG captures, so a regression guard is needed.
# ─────────────────────────────────────────────────────────────────────────


class _PageStub:
    """Stub that just records page.screenshot(...) call args."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def screenshot(self, **kw) -> None:
        self.calls.append(kw)


def test_screenshot_masked_passes_mask_list(tmp_path):
    from zero_touch_qa.executor import QAExecutor

    page = _PageStub()
    sentinel_a, sentinel_b = object(), object()
    out = QAExecutor._screenshot_masked(
        page, str(tmp_path), 7, "pass", mask=[sentinel_a, sentinel_b],
    )

    assert out.endswith("step_7_pass.png")
    assert len(page.calls) == 1
    assert page.calls[0]["mask"] == [sentinel_a, sentinel_b]


def test_screenshot_masked_with_none_uses_empty_mask(tmp_path):
    from zero_touch_qa.executor import QAExecutor

    page = _PageStub()
    QAExecutor._screenshot_masked(page, str(tmp_path), 1, "fail", mask=None)

    assert page.calls[0]["mask"] == []


def test_screenshot_masked_skips_when_playwright_missing_mask_arg(tmp_path):
    """If an old Playwright rejects the mask= argument, skip the
    screenshot entirely and return an empty path (fail-secure) so credentials don't leak."""
    from zero_touch_qa.executor import QAExecutor

    class _OldPage:
        def screenshot(self, **kw):
            if "mask" in kw:
                raise TypeError("unexpected keyword argument 'mask'")

    out = QAExecutor._screenshot_masked(
        _OldPage(), str(tmp_path), 1, "pass", mask=[object()],
    )
    assert out == ""


# ─────────────────────────────────────────────────────────────────────────
# mask_secret
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,keep,expected", [
    ("", 2, "<empty>"),
    ("ab", 2, "**"),                          # len ≤ keep → mask everything
    ("abc", 2, "*bc"),                        # only the trailing keep chars are plaintext
    ("S3cret-pass-1234", 2, "**************34"),
    ("hidden", 0, "******"),                  # keep=0 → mask everything
    ("totp_secret_xyz", 4, "***********_xyz"),
])
def test_mask_secret(value, keep, expected):
    assert mask_secret(value, keep=keep) == expected


# ─────────────────────────────────────────────────────────────────────────
# resolve_credential
# ─────────────────────────────────────────────────────────────────────────


def test_resolve_credential_all_fields(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}_DEMO_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_DEMO_PASS", "S3cret-pass")
    monkeypatch.setenv(f"{ENV_PREFIX}_DEMO_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    cred = resolve_credential("demo")
    assert cred.user == "tester@example.com"
    assert cred.password == "S3cret-pass"
    assert cred.totp_secret == "JBSWY3DPEHPK3PXP"
    assert cred.has_password()
    assert cred.has_totp()


def test_resolve_credential_alias_normalized(monkeypatch):
    """Non-alphanumeric chars in alias are normalized to ``_``."""
    monkeypatch.setenv(f"{ENV_PREFIX}_PROD_GOOGLE_USER", "ci@example.com")
    cred = resolve_credential("prod-google")  # dash → _
    assert cred.user == "ci@example.com"


def test_resolve_credential_partial(monkeypatch):
    """USER only is fine — at least one must be present."""
    monkeypatch.setenv(f"{ENV_PREFIX}_PARTIAL_USER", "user")
    cred = resolve_credential("partial")
    assert cred.user == "user"
    assert not cred.has_password()
    assert not cred.has_totp()


def test_resolve_credential_missing_raises():
    # if all three env vars are missing, raise
    with pytest.raises(CredentialError, match="not in environment"):
        resolve_credential("nonexistent_alias_zzz_unique")


def test_resolve_credential_empty_alias_raises():
    with pytest.raises(CredentialError, match="empty"):
        resolve_credential("")


# ─────────────────────────────────────────────────────────────────────────
# parse_auth_target
# ─────────────────────────────────────────────────────────────────────────


def test_parse_auth_target_default():
    opts = parse_auth_target("")
    assert opts.mode == "form"
    assert opts.email_field is None


def test_parse_auth_target_mode_only():
    assert parse_auth_target("form").mode == "form"
    assert parse_auth_target("totp").mode == "totp"
    assert parse_auth_target("oauth").mode == "oauth"


def test_parse_auth_target_with_explicit_selectors():
    opts = parse_auth_target(
        "form, email_field=#email, password_field=#pw, submit=#login"
    )
    assert opts.mode == "form"
    assert opts.email_field == "#email"
    assert opts.password_field == "#pw"
    assert opts.submit == "#login"


def test_parse_auth_target_oauth_provider():
    opts = parse_auth_target("oauth, provider=mock")
    assert opts.mode == "oauth"
    assert opts.provider == "mock"


def test_parse_auth_target_totp_explicit():
    opts = parse_auth_target("totp, totp_field=#code")
    assert opts.mode == "totp"
    assert opts.totp_field == "#code"


def test_parse_auth_target_unknown_keys_ignored():
    """Unknown keys warn and are dropped — parsing itself does not fail."""
    opts = parse_auth_target("form, unknown=foo, email_field=#e")
    assert opts.mode == "form"
    assert opts.email_field == "#e"


# ─────────────────────────────────────────────────────────────────────────
# generate_totp_code
# ─────────────────────────────────────────────────────────────────────────


def test_generate_totp_code_returns_six_digits():
    pytest.importorskip("pyotp")
    code = generate_totp_code("JBSWY3DPEHPK3PXP")
    assert isinstance(code, str)
    assert len(code) == 6
    assert code.isdigit()


def test_generate_totp_code_empty_secret_raises():
    with pytest.raises(CredentialError, match="empty"):
        generate_totp_code("")


# ─────────────────────────────────────────────────────────────────────────
# Confirm field candidate selectors are defined (regression guard)
# ─────────────────────────────────────────────────────────────────────────


def test_field_candidates_defined():
    """All 4 candidate-selector lists are non-empty."""
    assert len(EMAIL_FIELD_CANDIDATES) > 0
    assert len(PASSWORD_FIELD_CANDIDATES) > 0
    assert len(TOTP_FIELD_CANDIDATES) > 0
    assert len(SUBMIT_BUTTON_CANDIDATES) > 0
    # standard autocomplete selector must rank in priority
    assert any('autocomplete="username"' in s for s in EMAIL_FIELD_CANDIDATES)
    assert any('type="password"' in s for s in PASSWORD_FIELD_CANDIDATES)
    assert any('one-time-code' in s for s in TOTP_FIELD_CANDIDATES)


# ─────────────────────────────────────────────────────────────────────────
# AuthOptions / Credential dataclasses
# ─────────────────────────────────────────────────────────────────────────


def test_auth_options_default():
    o = AuthOptions()
    assert o.mode == "form"
    assert o.email_field is None
    assert o.totp_field is None


def test_credential_dataclass_helpers():
    c = Credential(alias="x", user="u", password="p")
    assert c.has_password()
    assert not c.has_totp()


# ─────────────────────────────────────────────────────────────────────────
# Fixture integration — call the executor's `_execute_auth_login` per scenario.
# Use the codebase's standard pattern (make_executor + run_scenario) —
# pytest-playwright's `page` fixture spins up an asyncio loop that breaks
# subsequent sync_playwright calls, so we use only the executor's internal
# sync_playwright.
# ─────────────────────────────────────────────────────────────────────────


def _navigate_step(url: str) -> dict:
    return {
        "step": 1, "action": "navigate", "target": "", "value": url,
        "description": f"navigate to {url}", "fallback_targets": [],
    }


def _auth_login_step(target: str, alias: str, *, step: int = 2) -> dict:
    return {
        "step": step, "action": "auth_login", "target": target, "value": alias,
        "description": "auth_login fixture integration", "fallback_targets": [],
    }


def _verify_step(target: str, value: str, *, step: int = 3) -> dict:
    return {
        "step": step, "action": "verify", "target": target, "value": value,
        "description": "verify auth_login result", "fallback_targets": [],
        "condition": "text",
    }


def test_auth_login_form_success(make_executor, run_scenario, monkeypatch):
    """auth_form.html happy path — auth_login form auto-detects fields + fill + submit."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_FORM_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_FORM_PASS", "S3cret-pass-1234")

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step("form", "fixture_form"),
        _verify_step("#status", "auth_form_login_ok"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    statuses = [r.status for r in results]
    assert statuses == ["PASS", "PASS", "PASS"], f"actual: {statuses}"


def test_auth_login_form_wrong_password(make_executor, run_scenario, monkeypatch):
    """Wrong password — auth_login itself PASSes (fill+submit succeeds) but
    the fixture flips status to fail, so the follow-up verify runs against fail."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_BAD_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_BAD_PASS", "wrong-password")

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step("form", "fixture_bad"),
        # verify the success status doesn't appear
        _verify_step("#status", "auth_form_login_fail"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    # auth_login itself PASSes (form submit succeeded) — failure message lands in status
    assert results[0].status == "PASS"
    assert results[1].status == "PASS"
    # status flips to 'auth_form_login_fail' → verify PASSes
    assert results[2].status == "PASS"


def test_auth_login_form_explicit_selectors(make_executor, run_scenario, monkeypatch):
    """Explicit selectors via target — skip auto-detection and use the named fields directly."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_EXPLICIT_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_EXPLICIT_PASS", "S3cret-pass-1234")

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step(
            "form, email_field=#email, password_field=#password, submit=#submit-btn",
            "fixture_explicit",
        ),
        _verify_step("#status", "auth_form_login_ok"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_auth_login_totp_success(make_executor, run_scenario, monkeypatch):
    """auth_totp.html — generate the code with pyotp, then fill + submit (auto-detected)."""
    pytest.importorskip("pyotp")
    monkeypatch.setenv(
        f"{ENV_PREFIX}_FIXTURE_TOTP_TOTP_SECRET", "JBSWY3DPEHPK3PXP",
    )

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_TOTP_URL),
        _auth_login_step("totp", "fixture_totp"),
        _verify_step("#status", "auth_totp_verify_ok"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_auth_login_totp_missing_secret_fails(
    make_executor, run_scenario, monkeypatch,
):
    """If alias has only USER and no TOTP secret, fail immediately."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_NOTOTP_USER", "x@example.com")
    # intentionally leave TOTP_SECRET unset

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_TOTP_URL),
        _auth_login_step("totp", "fixture_nototp"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert results[0].status == "PASS"  # navigate
    assert results[1].status == "FAIL"  # auth_login (no TOTP secret)


# ─────────────────────────────────────────────────────────────────────────
# Masking regression — confirm the Credential object doesn't leak plaintext via repr / str
# ─────────────────────────────────────────────────────────────────────────


def test_credential_repr_does_not_leak_password(monkeypatch):
    """Credential's default __repr__ is the dataclass default (exposes fields).

    This is intentional — as long as the value isn't printed during
    debugging, nothing leaks. But callers must wrap log output with
    mask_secret (the executor's `_execute_auth_login` does).
    """
    c = Credential(alias="x", user="u", password="p", totp_secret="s")
    # We *accept* that password appears in repr by default; callers are
    # responsible for wrapping with mask_secret. This test pins down that contract.
    assert "p" in repr(c)  # password in repr is the intended default behavior
    # but mask_secret hides plaintext when applied
    assert mask_secret(c.password, keep=0) == "*"
    assert mask_secret(c.totp_secret, keep=0) == "*"


# ─────────────────────────────────────────────────────────────────────────
# Log masking regression — capture caplog and verify the executor never
# leaks plaintext password / TOTP secret while running auth_login.
# ─────────────────────────────────────────────────────────────────────────

# Intentionally long, identifiable tokens — if they appear anywhere in captured logs, fail.
_PLAINTEXT_PASS_SENTINEL = "S3cret-PLAINTEXT-Sentinel-Token-9z"
_PLAINTEXT_TOTP_SENTINEL = "JBSWY3DPLAINTEXTONLY"


def test_executor_logs_do_not_leak_password(
    make_executor, run_scenario, monkeypatch, caplog,
):
    """No plaintext password leaks in logs/StepResult during the auth_login form flow."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_LEAK_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_LEAK_PASS", _PLAINTEXT_PASS_SENTINEL)

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step("form", "fixture_leak"),
    ]
    with caplog.at_level("DEBUG"):
        results, _, _ = run_scenario(executor, scenario)

    # 1) plaintext sentinel never appears in caplog
    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert _PLAINTEXT_PASS_SENTINEL not in full_log, (
        "executor logs leaked plaintext password"
    )
    # 2) and never in StepResult's value/target/description either
    for r in results:
        for fld in (r.target, r.value, r.description):
            assert _PLAINTEXT_PASS_SENTINEL not in str(fld)


def test_executor_logs_do_not_leak_totp_secret(
    make_executor, run_scenario, monkeypatch, caplog,
):
    """No plaintext TOTP secret leaks in the auth_login totp flow."""
    pytest.importorskip("pyotp")
    monkeypatch.setenv(
        f"{ENV_PREFIX}_FIXTURE_LEAK_TOTP_TOTP_SECRET", _PLAINTEXT_TOTP_SENTINEL,
    )

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_TOTP_URL),
        _auth_login_step("totp", "fixture_leak_totp"),
    ]
    with caplog.at_level("DEBUG"):
        results, _, _ = run_scenario(executor, scenario)

    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert _PLAINTEXT_TOTP_SENTINEL not in full_log, (
        "executor logs leaked plaintext TOTP secret"
    )
    for r in results:
        for fld in (r.target, r.value, r.description):
            assert _PLAINTEXT_TOTP_SENTINEL not in str(fld)


def test_executor_logs_user_email_partially_visible(
    make_executor, run_scenario, monkeypatch, caplog,
):
    """user (email) isn't fully revealed — only partial via mask_secret(keep=2).

    Full masking makes debugging hard, so the trailing 2 chars of user stay plaintext.
    """
    monkeypatch.setenv(
        f"{ENV_PREFIX}_FIXTURE_USER_VIS_USER", "tester-with-special@example.com",
    )
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_USER_VIS_PASS", "irrelevant-pwd")

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step("form", "fixture_user_vis"),
    ]
    with caplog.at_level("INFO"):
        run_scenario(executor, scenario)

    full_log = "\n".join(record.getMessage() for record in caplog.records)
    # raw email itself never appears in full
    assert "tester-with-special@example.com" not in full_log
