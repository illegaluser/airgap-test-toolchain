"""인증 시뮬 fixture 사이트 + verify_profile / storage 흐름 통합 회귀 (개선 3).

배경:
    2026-05-02 사용자 사고. ``dpg`` storage 의 인증 쿠키 ``piolb`` 가 어제 만료된
    상태에서 ``verify_profile`` 이 ok=true 로 통과시켜 사용자가 비로그인 화면을
    봤음. ``verify_profile`` 이 status<400 + same-host 만 검사했고 본문에
    "로그인" 단어가 있는지는 보지 않았던 것.

본 모듈은 fixture HTTP 사이트로 동등한 흐름을 재현해 회귀 가드:
    - 정상 storage_state 로 보호 페이지 진입 → "로그아웃" 단어 노출 확인
    - 만료 임박 cookie (Max-Age=2) 시뮬 → 잠시 후 다시 진입 시 비로그인 안내
    - ``_storage_alive_cookie_count_for_host`` 가 만료 폐기 동작 사전 감지

Playwright sync API 가 직접 실행되므로 ~5~10s. e2e 마커.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from _authn_fixture_site import start_authn_site


@pytest.fixture
def authn_site():
    httpd, base_url = start_authn_site()
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()


def _seed_storage_via_browser(base_url: str, dump_path: Path, *, short_lived: bool = False) -> None:
    """fixture 사이트에 로그인해서 storage_state 를 dump."""
    from playwright.sync_api import sync_playwright

    login_path = "/login_short" if short_lived else "/login"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{base_url}{login_path}?user=qa-tester", wait_until="load")
        # /mypage 로 redirect 후 로그인된 화면 보여야.
        body = page.inner_text("body", timeout=5000)
        assert "환영" in body, f"로그인 직후 환영 페이지 진입 실패: {body[:200]}"
        context.storage_state(path=str(dump_path))
        browser.close()


@pytest.mark.e2e
def test_fresh_storage_authenticates_into_protected_page(authn_site: str, tmp_path: Path):
    """방금 발급된 storage_state 로 보호 페이지 진입 시 로그인 상태 유지."""
    from playwright.sync_api import sync_playwright

    storage = tmp_path / "fresh.storage.json"
    _seed_storage_via_browser(authn_site, storage)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage))
        page = context.new_page()
        page.goto(f"{authn_site}/secret", wait_until="load")
        body = page.inner_text("body", timeout=5000)
        assert "로그아웃" in body, "로그인 상태 신호(로그아웃 단어) 없음"
        assert "로그인" not in body or "로그아웃" in body  # 보수적
        browser.close()


@pytest.mark.e2e
def test_expired_storage_falls_through_to_login_form(authn_site: str, tmp_path: Path):
    """Max-Age=2 쿠키로 시드된 storage 가 잠시 후 비로그인 상태로 떨어지는지.

    회귀 가드: 만료된 storage 가 보호 페이지 진입을 못 하고 fixture 가 / 로
    redirect 하면 본문에 "로그인" 단어 노출. 우리 ``_body_looks_unauthenticated``
    휴리스틱이 이 신호를 정확히 잡는지도 함께 검증.
    """
    from playwright.sync_api import sync_playwright
    from zero_touch_qa.auth_profiles import _body_looks_unauthenticated

    storage = tmp_path / "short.storage.json"
    _seed_storage_via_browser(authn_site, storage, short_lived=True)

    # 쿠키 만료 대기 (Max-Age=2 + 여유).
    time.sleep(3)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage))
        page = context.new_page()
        page.goto(f"{authn_site}/secret", wait_until="load")
        body = page.inner_text("body", timeout=5000)
        # fixture 의 / 페이지로 redirect 됐어야 (로그인 폼).
        assert _body_looks_unauthenticated(body), (
            f"비로그인 신호 휴리스틱이 fail (fixture 동작 또는 휴리스틱 회귀): "
            f"{body[:200]}"
        )
        browser.close()


@pytest.mark.e2e
def test_storage_alive_cookie_count_detects_expired_storage(authn_site: str, tmp_path: Path):
    """``_storage_alive_cookie_count_for_host`` 가 fixture 만료 시뮬 storage 를 사전 감지."""
    from urllib.parse import urlparse
    from zero_touch_qa.auth_profiles import _storage_alive_cookie_count_for_host

    storage = tmp_path / "short.storage.json"
    _seed_storage_via_browser(authn_site, storage, short_lived=True)
    time.sleep(3)

    host = urlparse(authn_site).hostname or ""
    alive, total = _storage_alive_cookie_count_for_host(storage, host)
    # Max-Age=2 쿠키가 사전에 expires 시각을 받은 상태이므로 storage 안에선
    # expires < now 로 만료 판정 — alive 0 이어야.
    assert total >= 1, "fixture 가 host 도메인 쿠키를 한 개도 안 박았음 — 시드 회귀"
    assert alive == 0, (
        f"만료된 쿠키가 alive 로 잡힘 — pre-check 회귀. alive={alive}, total={total}"
    )
