"""verify 액션 — 4 케이스.

dify-chatflow.yaml Planner prompt 의 verify 규칙을 반영:
- value 가 빈 문자열이면 "그 요소가 visible 한지" 만 확인
- value 에는 페이지에 실제로 보일 텍스트만 넣는다 (메타 설명문 금지)
"""

from playwright.sync_api import Page, expect


def test_verify_visible_without_value(page: Page, fixture_url):
    """value 없이 visibility 만 검증."""
    page.goto(fixture_url("verify.html"))
    expect(page.locator("#visible-item")).to_be_visible()


def test_verify_hidden_element_is_not_visible(page: Page, fixture_url):
    """display:none element 는 visible 이 아니다."""
    page.goto(fixture_url("verify.html"))
    expect(page.locator("#hidden-item")).not_to_be_visible()


def test_verify_text_contains_expected_value(page: Page, fixture_url):
    """element 가 포함한 텍스트가 기대값을 substring 으로 포함."""
    page.goto(fixture_url("verify.html"))
    text = page.locator("#result-text").inner_text()
    assert "검색결과" in text
    assert "123" in text


def test_verify_main_area_has_children(page: Page, fixture_url):
    """'검색결과 목록 존재 여부' 같은 영역 visibility — main 안에 자식 p 가 존재."""
    page.goto(fixture_url("verify.html"))
    expect(page.locator("#content-area")).to_be_visible()
    # main 안에 p 태그가 최소 2 개 있음
    assert page.locator("#content-area p").count() >= 2
