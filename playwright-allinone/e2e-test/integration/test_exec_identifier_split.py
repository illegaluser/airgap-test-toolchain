"""Regression guard for f1761f2 — executor 식별자 줄바꿈 정규화.

사용자 보고 (adc30a1e3fa3 step 21): multi-line nav link
``<a><span>디지털융합플랫폼</span><span>페르소나</span><span>ChatBot</span></a>``
의 ``innerText`` 가 ``"디지털융합플랫폼\\n페르소나\\nChatBot"`` 인데,
fix 이전엔 첫 줄만 잡혀 ``name="디지털융합플랫폼"`` 으로 잘려 회귀 .py 에 emit
되어 ``exact=True`` 와 합쳐져 30s timeout 으로 회귀 실패.

fix 는 ``split(/\\s+/).filter(Boolean).join(' ')`` 로 줄바꿈/다중 공백을 단일
공백으로 평탄화해 *전체* 이름 ``"디지털융합플랫폼 페르소나 ChatBot"`` 을 보존.

이 슈트는 ``_extract_stable_selector`` 가 multi-line nav link 에서 풀 네임을
보존하는지 직접 확인한다 — JS 가 브라우저에서 실행되므로 unit 슬롯이 아닌
integration 슬롯 (pre-push) 에서 동작.
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import _extract_stable_selector


@pytest.mark.integration
def test_extract_stable_selector_preserves_multiline_name(page, fixture_url):
    """multi-line nav link 의 식별자가 줄바꿈을 단일 공백으로 평탄화해 보존된다."""
    page.goto(fixture_url("identifier_split_multiline_nav.html"))

    link = page.locator("a.multiline-nav")
    selector = _extract_stable_selector(link)

    assert selector, "비어 있음 — 합성 이름이 Playwright accessible name 과 mismatch 가능"
    assert selector.startswith("role=link"), f"role=link 로 시작해야 함: {selector!r}"
    assert "디지털융합플랫폼 페르소나 ChatBot" in selector, (
        f"줄바꿈 잘림 회귀 — 첫 줄만 잡힘: {selector!r}"
    )
    assert "exact=true" in selector, f"exact=true 누락: {selector!r}"
