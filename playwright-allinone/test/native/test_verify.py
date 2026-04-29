"""verify action — 4 cases.

Mirrors the verify rules from the dify-chatflow.yaml Planner prompt:
- if value is empty, only confirm the element is visible
- value must be text that actually shows on the page (no meta descriptions)
"""

from playwright.sync_api import Page, expect


def test_verify_visible_without_value(page: Page, fixture_url):
    """Verify visibility only, with no value."""
    page.goto(fixture_url("verify.html"))
    expect(page.locator("#visible-item")).to_be_visible()


def test_verify_hidden_element_is_not_visible(page: Page, fixture_url):
    """A display:none element is not visible."""
    page.goto(fixture_url("verify.html"))
    expect(page.locator("#hidden-item")).not_to_be_visible()


def test_verify_text_contains_expected_value(page: Page, fixture_url):
    """The element's text contains the expected value as a substring."""
    page.goto(fixture_url("verify.html"))
    text = page.locator("#result-text").inner_text()
    assert "검색결과" in text
    assert "123" in text


def test_verify_main_area_has_children(page: Page, fixture_url):
    """Region visibility like "search results list present" — main has child p tags."""
    page.goto(fixture_url("verify.html"))
    expect(page.locator("#content-area")).to_be_visible()
    # main contains at least 2 p tags
    assert page.locator("#content-area p").count() >= 2
