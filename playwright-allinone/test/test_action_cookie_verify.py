"""cookie_verify 액션 (Sprint 6 / 측정 액션 3/5) 테스트.

cookie 는 file:// origin 에서 attach 가 어렵고 fixture HTTP server 띄우면 비용
큼. 핵심 비교 로직은 ``page.context.cookies()`` 결과를 monkeypatch 로 가짜
응답해 검증. 도메인 필터 + 정확 일치 + 존재만 검증 등 모든 분기 커버.

실 도메인 e2e 는 별 트랙 (트랙 2 Phase B2 외부 벤치마크) 에서 검증.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zero_touch_qa.executor import QAExecutor, VerificationAssertionError


def _fake_page(cookies: list[dict]):
    """page.context.cookies() 가 ``cookies`` 를 돌려주는 최소 mock."""
    ctx = SimpleNamespace(cookies=lambda: cookies)
    return SimpleNamespace(context=ctx)


def _stub_screenshot(executor: QAExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
    """_screenshot 호출은 mock page 에서 fail 하므로 None 반환으로 무력화."""
    monkeypatch.setattr(executor, "_screenshot", lambda *a, **kw: None)


def test_cookie_verify_rejects_empty_name(make_executor):
    executor: QAExecutor = make_executor()
    with pytest.raises(ValueError, match="cookie name 이 비어있음"):
        executor._execute_cookie_verify(
            _fake_page([]),
            {"action": "cookie_verify", "target": "", "value": "x"},
            "",
        )


def test_cookie_verify_exact_match(make_executor, monkeypatch):
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "session_id", "value": "abc123", "domain": "example.com", "path": "/"},
    ])
    result = executor._execute_cookie_verify(
        page,
        {"action": "cookie_verify", "target": "session_id", "value": "abc123",
         "description": ""},
        "",
    )
    assert result.status == "PASS"


def test_cookie_verify_existence_only(make_executor, monkeypatch):
    """value 빈 문자열 — cookie 존재만 검증."""
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "session_id", "value": "anything", "domain": "example.com", "path": "/"},
    ])
    result = executor._execute_cookie_verify(
        page,
        {"action": "cookie_verify", "target": "session_id", "value": "",
         "description": ""},
        "",
    )
    assert result.status == "PASS"


def test_cookie_verify_missing_fails(make_executor, monkeypatch):
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "other", "value": "x", "domain": "example.com"},
    ])
    with pytest.raises(VerificationAssertionError, match="cookie 미존재"):
        executor._execute_cookie_verify(
            page,
            {"action": "cookie_verify", "target": "session_id", "value": ""},
            "",
        )


def test_cookie_verify_value_mismatch_fails(make_executor, monkeypatch):
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "session_id", "value": "OTHER", "domain": "example.com"},
    ])
    with pytest.raises(VerificationAssertionError, match="값 불일치"):
        executor._execute_cookie_verify(
            page,
            {"action": "cookie_verify", "target": "session_id", "value": "abc123"},
            "",
        )


def test_cookie_verify_domain_filter_matches(make_executor, monkeypatch):
    """target=NAME@DOMAIN 으로 특정 도메인 cookie 만 매치."""
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "sid", "value": "wrong", "domain": "other.com", "path": "/"},
        {"name": "sid", "value": "abc",   "domain": "example.com", "path": "/"},
    ])
    result = executor._execute_cookie_verify(
        page,
        {"action": "cookie_verify", "target": "sid@example.com", "value": "abc"},
        "",
    )
    assert result.status == "PASS"


def test_cookie_verify_domain_leading_dot_normalized(make_executor, monkeypatch):
    """domain ``.example.com`` 과 ``example.com`` 동등 매치."""
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "sid", "value": "abc", "domain": ".example.com", "path": "/"},
    ])
    result = executor._execute_cookie_verify(
        page,
        {"action": "cookie_verify", "target": "sid@example.com", "value": "abc"},
        "",
    )
    assert result.status == "PASS"


def test_cookie_verify_domain_filter_no_match_fails(make_executor, monkeypatch):
    executor: QAExecutor = make_executor()
    _stub_screenshot(executor, monkeypatch)
    page = _fake_page([
        {"name": "sid", "value": "abc", "domain": "other.com"},
    ])
    with pytest.raises(VerificationAssertionError, match="cookie 미존재"):
        executor._execute_cookie_verify(
            page,
            {"action": "cookie_verify", "target": "sid@example.com", "value": "abc"},
            "",
        )
