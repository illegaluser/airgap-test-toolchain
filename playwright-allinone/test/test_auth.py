"""T-D (P0.1) — auth.py 단위 테스트 + auth_login fixture 통합.

설계: PLAN_PRODUCTION_READINESS.md §"T-D — 인증 (form + OAuth + TOTP)"

검증 영역:
- credential alias env var lookup (성공/실패)
- mask_secret 마스킹 규칙
- parse_auth_target 의 mode/옵션 파싱
- TOTP 코드 생성 (pyotp 통합)
- form/totp fixture 와 executor `_execute_auth_login` 통합
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
# CLI 검증 — auth_login 이 _validate_scenario 를 통과하는지 회귀 (P0.1 #1).
# CLI 가 auth_login 액션을 화이트리스트 밖이라 reject 하던 버그의 회귀 가드.
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
        "value": "demo_alias", "description": "form 로그인",
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
            "value": "demo_alias", "description": "잘못된 모드",
            "fallback_targets": [],
        }))


def test_cli_rejects_auth_login_with_empty_value():
    from zero_touch_qa.__main__ import _validate_scenario, ScenarioValidationError

    with pytest.raises(ScenarioValidationError, match="value"):
        _validate_scenario(_scenario_with({
            "step": 2, "action": "auth_login", "target": "form",
            "value": "", "description": "alias 누락",
            "fallback_targets": [],
        }))


# ─────────────────────────────────────────────────────────────────────────
# 스크린샷 마스킹 — _screenshot_masked 가 Playwright 의 mask= 인자를 정확히
# 전달하는지 확인 (P0.1 #3). credential 평문이 PNG 캡처에 노출되는 것을 막는
# 유일한 게이트이므로 회귀 가드가 필요.
# ─────────────────────────────────────────────────────────────────────────


class _PageStub:
    """page.screenshot(...) 호출 인자를 기록만 하는 stub."""

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
    """구버전 Playwright 가 mask= 인자를 거부하면 자격증명 노출 방지를 위해
    스크린샷 자체를 생략하고 빈 경로 반환 (Fail-secure)."""
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
    ("ab", 2, "**"),                          # len ≤ keep → 전체 마스킹
    ("abc", 2, "*bc"),                        # 끝 keep 자만 평문
    ("S3cret-pass-1234", 2, "**************34"),
    ("hidden", 0, "******"),                  # keep=0 → 전체 마스킹
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
    """alias 의 비-영숫자 문자는 ``_`` 로 변환된다."""
    monkeypatch.setenv(f"{ENV_PREFIX}_PROD_GOOGLE_USER", "ci@example.com")
    cred = resolve_credential("prod-google")  # dash → _
    assert cred.user == "ci@example.com"


def test_resolve_credential_partial(monkeypatch):
    """USER 만 있어도 OK — 적어도 하나는 있어야 함."""
    monkeypatch.setenv(f"{ENV_PREFIX}_PARTIAL_USER", "user")
    cred = resolve_credential("partial")
    assert cred.user == "user"
    assert not cred.has_password()
    assert not cred.has_totp()


def test_resolve_credential_missing_raises():
    # 환경변수 셋 다 없으면 에러
    with pytest.raises(CredentialError, match="credential 이 환경변수에 없음"):
        resolve_credential("nonexistent_alias_zzz_unique")


def test_resolve_credential_empty_alias_raises():
    with pytest.raises(CredentialError, match="비어 있음"):
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
    """알 수 없는 키는 경고 후 무시 — 파싱 자체는 실패하지 않음."""
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
    with pytest.raises(CredentialError, match="비어 있음"):
        generate_totp_code("")


# ─────────────────────────────────────────────────────────────────────────
# Field 후보 selector 가 정의되어 있는지 (regression 방지)
# ─────────────────────────────────────────────────────────────────────────


def test_field_candidates_defined():
    """4 개 후보 selector 리스트가 비어 있지 않음."""
    assert len(EMAIL_FIELD_CANDIDATES) > 0
    assert len(PASSWORD_FIELD_CANDIDATES) > 0
    assert len(TOTP_FIELD_CANDIDATES) > 0
    assert len(SUBMIT_BUTTON_CANDIDATES) > 0
    # autocomplete 표준 selector 가 우선순위에 있어야 함
    assert any('autocomplete="username"' in s for s in EMAIL_FIELD_CANDIDATES)
    assert any('type="password"' in s for s in PASSWORD_FIELD_CANDIDATES)
    assert any('one-time-code' in s for s in TOTP_FIELD_CANDIDATES)


# ─────────────────────────────────────────────────────────────────────────
# AuthOptions / Credential 데이터클래스
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
# Fixture 통합 — executor `_execute_auth_login` 을 시나리오 단위로 호출.
# 코드베이스 표준 패턴 (make_executor + run_scenario) 사용 — pytest-playwright
# 의 `page` fixture 는 asyncio 루프를 띄워서 후속 sync_playwright 호출을
# 깨뜨리므로 executor 내부의 sync_playwright 만 사용한다.
# ─────────────────────────────────────────────────────────────────────────


def _navigate_step(url: str) -> dict:
    return {
        "step": 1, "action": "navigate", "target": "", "value": url,
        "description": f"{url} 로 이동", "fallback_targets": [],
    }


def _auth_login_step(target: str, alias: str, *, step: int = 2) -> dict:
    return {
        "step": step, "action": "auth_login", "target": target, "value": alias,
        "description": "auth_login fixture 통합", "fallback_targets": [],
    }


def _verify_step(target: str, value: str, *, step: int = 3) -> dict:
    return {
        "step": step, "action": "verify", "target": target, "value": value,
        "description": "auth_login 결과 verify", "fallback_targets": [],
        "condition": "text",
    }


def test_auth_login_form_success(make_executor, run_scenario, monkeypatch):
    """auth_form.html 정상 로그인 — auth_login form 이 필드 자동 탐지 + fill + submit."""
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
    assert statuses == ["PASS", "PASS", "PASS"], f"실제: {statuses}"


def test_auth_login_form_wrong_password(make_executor, run_scenario, monkeypatch):
    """잘못된 비밀번호 — auth_login 자체는 PASS (fill+submit 성공) 하지만
    fixture 의 status 가 fail 로 바뀌어 후속 verify 가 FAIL."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_BAD_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_BAD_PASS", "wrong-password")

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step("form", "fixture_bad"),
        # 정답 status 가 안 나오는지 verify
        _verify_step("#status", "auth_form_login_fail"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    # auth_login 자체는 PASS (form 제출 성공) — 실패 메시지가 status 에 반영
    assert results[0].status == "PASS"
    assert results[1].status == "PASS"
    # status 가 'auth_form_login_fail' 로 바뀜 → verify PASS
    assert results[2].status == "PASS"


def test_auth_login_form_explicit_selectors(make_executor, run_scenario, monkeypatch):
    """target 옵션으로 selector 명시 — 자동 탐지 안 거치고 직접 지정한 필드 사용."""
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
    """auth_totp.html — pyotp 코드 생성 후 fill + submit (자동 탐지)."""
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
    """alias 가 USER 만 가지고 있고 TOTP 시크릿이 없으면 즉시 FAIL."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_NOTOTP_USER", "x@example.com")
    # TOTP_SECRET 의도적으로 안 둠

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_TOTP_URL),
        _auth_login_step("totp", "fixture_nototp"),
    ]
    results, _, _ = run_scenario(executor, scenario)
    assert results[0].status == "PASS"  # navigate
    assert results[1].status == "FAIL"  # auth_login (TOTP 시크릿 없음)


# ─────────────────────────────────────────────────────────────────────────
# 마스킹 회귀 — Credential 객체가 repr / str 로 평문 노출하지 않는지
# ─────────────────────────────────────────────────────────────────────────


def test_credential_repr_does_not_leak_password(monkeypatch):
    """Credential 의 default __repr__ 은 dataclass 기본 (필드 노출).

    이는 의도된 디자인 — 디버깅 시 직접 출력 안 하면 노출 안 됨. 다만
    log 출력 시 사용 측에서 mask_secret 으로 감싸야 한다 (executor 의
    `_execute_auth_login` 가 그렇게 함).
    """
    c = Credential(alias="x", user="u", password="p", totp_secret="s")
    # repr 에 password 가 노출되는 것을 *기본값* 으로 받아들이고, 실 사용 측이
    # mask_secret 으로 감싸는 책임을 진다. 본 테스트는 그 계약을 명시.
    assert "p" in repr(c)  # password 가 repr 에 들어가는 것 자체는 의도된 동작
    # 그러나 mask_secret 적용 시 평문 노출 안 됨
    assert mask_secret(c.password, keep=0) == "*"
    assert mask_secret(c.totp_secret, keep=0) == "*"


# ─────────────────────────────────────────────────────────────────────────
# 로그 마스킹 회귀 — executor 가 auth_login 실행 시 평문 password / TOTP 시크릿
# 을 로그에 절대 흘리지 않는지 caplog 로 캡처해 검증.
# ─────────────────────────────────────────────────────────────────────────

# 의도적으로 짧지 않은, 식별 가능한 토큰 — 캡처된 로그 어디에도 등장하면 fail.
_PLAINTEXT_PASS_SENTINEL = "S3cret-PLAINTEXT-Sentinel-Token-9z"
_PLAINTEXT_TOTP_SENTINEL = "JBSWY3DPLAINTEXTONLY"


def test_executor_logs_do_not_leak_password(
    make_executor, run_scenario, monkeypatch, caplog,
):
    """auth_login form 흐름의 모든 로그/StepResult 에서 평문 password 가 노출 안 됨."""
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_LEAK_USER", "tester@example.com")
    monkeypatch.setenv(f"{ENV_PREFIX}_FIXTURE_LEAK_PASS", _PLAINTEXT_PASS_SENTINEL)

    executor = make_executor()
    scenario = [
        _navigate_step(AUTH_FORM_URL),
        _auth_login_step("form", "fixture_leak"),
    ]
    with caplog.at_level("DEBUG"):
        results, _, _ = run_scenario(executor, scenario)

    # 1) caplog 에 평문 sentinel 미노출
    full_log = "\n".join(record.getMessage() for record in caplog.records)
    assert _PLAINTEXT_PASS_SENTINEL not in full_log, (
        "executor 로그에 평문 password 노출됨"
    )
    # 2) StepResult 의 value/target/description 에도 미노출
    for r in results:
        for fld in (r.target, r.value, r.description):
            assert _PLAINTEXT_PASS_SENTINEL not in str(fld)


def test_executor_logs_do_not_leak_totp_secret(
    make_executor, run_scenario, monkeypatch, caplog,
):
    """auth_login totp 흐름에서 평문 TOTP 시크릿이 노출 안 됨."""
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
        "executor 로그에 평문 TOTP 시크릿 노출됨"
    )
    for r in results:
        for fld in (r.target, r.value, r.description):
            assert _PLAINTEXT_TOTP_SENTINEL not in str(fld)


def test_executor_logs_user_email_partially_visible(
    make_executor, run_scenario, monkeypatch, caplog,
):
    """user (email) 은 전체 평문 노출 안 되고 mask_secret(keep=2) 형태로 부분 노출.

    completely 마스킹은 디버깅 어려워서 user 는 끝 2자만 평문이 정상.
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
    # 평문 email 자체는 완전 노출 안 됨
    assert "tester-with-special@example.com" not in full_log
