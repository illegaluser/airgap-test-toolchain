"""SUT 호환성 진단 모듈(compat_diag) 단위 테스트.

로컬 fixture HTML 만 사용 — 외부 사이트 의존 없음 (airgap 호환).
5종 픽스처로 5종 판정 카테고리 중 reachable 한 4종 (compatible /
limited / incompatible:closed-shadow / unknown) 을 검증한다.
``incompatible:captcha`` 는 별도 mock script 픽스처로 검증.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.compat_diag import CompatReport, scan_dom


# settle_ms 는 hook 카운터 수집을 위한 짧은 대기. 500~1500ms.


def test_clean_fixture_is_compatible(fixture_url):
    report = scan_dom(fixture_url("compat_clean.html"), settle_ms=500)
    assert report.verdict == "compatible", report.reasons
    assert report.signals["shadow_open"] == 0
    assert report.signals["shadow_closed"] == 0
    assert report.signals["websocket_count"] == 0
    assert report.signals["captcha_detected"] is False


def test_open_shadow_is_compatible(fixture_url):
    """open Shadow DOM 은 Playwright 가 piercing 가능 → compatible."""
    report = scan_dom(fixture_url("shadow_open.html"), settle_ms=500)
    assert report.verdict == "compatible", report.reasons
    assert report.signals["shadow_open"] >= 1
    assert report.signals["shadow_closed"] == 0


def test_closed_shadow_is_incompatible(fixture_url):
    """closed Shadow DOM 은 브라우저 정책상 자동화 불가 → 즉시 FAIL."""
    report = scan_dom(fixture_url("shadow_closed.html"), settle_ms=500)
    assert report.verdict == "incompatible:closed-shadow"
    assert report.signals["shadow_closed"] >= 1


def test_websocket_is_limited(fixture_url):
    """WebSocket 호출 = 14-DSL 로 mock 불가 → limited."""
    report = scan_dom(fixture_url("compat_websocket.html"), settle_ms=1500)
    assert report.verdict == "limited", report.reasons
    assert report.signals["websocket_count"] >= 1
    assert any("WebSocket" in r for r in report.reasons)


def test_canvas_heavy_is_limited(fixture_url):
    """canvas 면적 비율 > 30% → visual-only 경고 → limited."""
    report = scan_dom(fixture_url("compat_canvas.html"), settle_ms=500)
    assert report.verdict == "limited", report.reasons
    assert report.signals["canvas_area_ratio"] > 0.3
    assert any("canvas" in r for r in report.reasons)


def test_report_to_json_roundtrip(fixture_url):
    report = scan_dom(fixture_url("compat_clean.html"), settle_ms=500)
    payload = report.to_json()
    import json

    data = json.loads(payload)
    assert data["url"].endswith("compat_clean.html")
    assert data["verdict"] == "compatible"
    assert "signals" in data


def test_report_to_html_contains_verdict(fixture_url):
    report = scan_dom(fixture_url("compat_clean.html"), settle_ms=500)
    html_text = report.to_html()
    assert "<title>Compat report" in html_text
    assert "compatible" in html_text
    assert "Signals" in html_text


def test_unreachable_url_is_unknown():
    """페이지 로드 실패 → unknown verdict + 사유 캡처."""
    # 0.0.0.0 으로 보장된 connection refused; 짧은 timeout 으로 빠르게 unknown 진입.
    report = scan_dom("http://127.0.0.1:1/", timeout_ms=2000, settle_ms=100)
    assert report.verdict == "unknown"
    assert any("page load failed" in r for r in report.reasons)


def test_classify_captcha_synthetic():
    """CAPTCHA 신호는 fixture 외부 의존 없이 _classify 단위로 검증."""
    from zero_touch_qa.compat_diag import _classify

    signals = {
        "shadow_open": 0,
        "shadow_closed": 0,
        "websocket_count": 0,
        "event_source_count": 0,
        "dialog_calls": [],
        "iframes": [],
        "captcha_detected": True,
        "canvas_area_ratio": 0.0,
        "svg_area_ratio": 0.0,
        "frameworks": {},
    }
    report = _classify("http://x/", signals)
    assert report.verdict == "incompatible:captcha"
    assert any("CAPTCHA" in r for r in report.reasons)


def test_classify_iframe_overflow():
    """iframe > 2 개 → limited."""
    from zero_touch_qa.compat_diag import _classify

    signals = {
        "shadow_open": 0,
        "shadow_closed": 0,
        "websocket_count": 0,
        "event_source_count": 0,
        "dialog_calls": [],
        "iframes": ["a.html", "b.html", "c.html"],
        "captcha_detected": False,
        "canvas_area_ratio": 0.0,
        "svg_area_ratio": 0.0,
        "frameworks": {},
    }
    report = _classify("http://x/", signals)
    assert report.verdict == "limited"
    assert any("iframe" in r for r in report.reasons)
