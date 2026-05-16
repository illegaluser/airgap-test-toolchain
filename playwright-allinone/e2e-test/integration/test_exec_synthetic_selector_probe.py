"""Regression guard for b4fc0a1 — 합성된 selector 가 매치 0건/2+건 시 drop.

증상: LLM 모드 재생은 모든 단계 PASS 인데 회귀 .py 재실행 시 특정 단계 실패.

원인: 자가 치유로 통과한 단계에 대해 element 의 안정 식별자 (role+accessible
name) 를 합성. 텍스트 평탄화 (multi-line/공백 정규화) 결과가 Playwright 의
accessible name 알고리즘과 미묘하게 달라 회귀에서 매치 안 됨.

조치: 합성 selector 를 회귀에 저장하기 전 *같은 페이지에서 정확히 1건* 매치
되는지 probe. 0건 → 알고리즘 mismatch, 2+건 → 모호 — 양쪽 모두 합성 selector
버리고 자가 치유의 실제 selector 로 fallback.

본 슈트는 ``_extract_stable_selector`` 가 (a) ambiguous 한 element (2+건 매치)
에서 빈 문자열을 반환하는지 가드.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import _extract_stable_selector


@pytest.mark.integration
def test_synthetic_selector_dropped_when_match_is_ambiguous(page, fixture_url):
    """동일 accessible name 의 link 2개 — 합성 role+name 이 probe 단계에서 drop."""
    page.goto(fixture_url("synthetic_selector_ambiguous.html"))
    # 두 link 다 role=link, name="Submit" — probe 가 count==2 잡고 drop.
    link = page.locator("a.dup").first
    selector = _extract_stable_selector(link)
    assert selector == "", (
        f"ambiguous match (2+건) 시 합성 selector drop 안 됨: {selector!r}. "
        f"회귀: 회귀 .py 가 모호한 selector 로 다른 element 클릭하거나 timeout."
    )


@pytest.mark.integration
def test_unique_role_name_match_returns_synthetic_selector(page, fixture_url):
    """1건 매치는 정상 — 합성 selector 가 ``role=link, name=..., exact=true`` 형태로 반환 (회귀 격리)."""
    # 기존 identifier_split fixture 의 단일 multi-line link 재사용 — 1건 매치.
    page.goto(fixture_url("identifier_split_multiline_nav.html"))
    link = page.locator("a.multiline-nav")
    selector = _extract_stable_selector(link)
    assert selector, "unique 매치인데도 drop 됐음 — probe 가 over-drop 회귀"
    assert selector.startswith("role=link"), f"role=link 시작 누락: {selector!r}"
    assert "exact=true" in selector, f"exact=true 누락: {selector!r}"
