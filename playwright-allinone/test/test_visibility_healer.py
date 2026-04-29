"""T-H — Visibility Healer unit tests.

Verifies that when codegen drops the hover step in a hover-then-click
sequence and the click then targets a hidden element, the executor
auto-recovers by hovering the ancestor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


FIXTURES_DIR = Path(__file__).parent / "fixtures"
HOVER_MENU_URL = (FIXTURES_DIR / "hover_menu.html").as_uri()


@pytest.fixture(scope="module")
def _ctx():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context()
        yield ctx
        ctx.close()
        browser.close()


@pytest.fixture
def page(_ctx):
    p = _ctx.new_page()
    p.goto(HOVER_MENU_URL)
    yield p
    p.close()


def test_submenu_is_hidden_initially(page):
    """Baseline — the submenu link is not visible before hover."""
    assert not page.locator("#link-about").is_visible()


def test_visibility_healer_makes_hidden_link_clickable(page, make_executor):
    """The visibility healer triggers li:hover and turns the hidden link visible."""
    executor = make_executor()
    loc = page.locator("#link-about").first
    assert not loc.is_visible()

    executor._heal_visibility(page, loc, step_id=99)

    # must be visible after hover.
    assert loc.is_visible()


def test_visibility_healer_noop_when_already_visible(page, make_executor):
    """No side effect when the element is already visible — stays visible."""
    executor = make_executor()
    loc = page.locator("#menu-company").first  # nav top — always visible
    assert loc.is_visible()
    executor._heal_visibility(page, loc, step_id=99)
    assert loc.is_visible()


# ── T-H (B + C) — multi-match + mobile/desktop dual rendering ──────────────


@pytest.fixture
def dual_match_page(_ctx):
    """Fixture where mobile drawer (hidden) + desktop GNB (visible) share the same label."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "dual_match_menu.html").as_uri())
    yield p
    p.close()


def test_resolver_prefers_visible_match_over_hidden(dual_match_page):
    """T-H (B) — resolver prefers a visible match over a hidden one for role+name.

    fixture: mobile drawer (transform:scale(0), DOM order 0) +
    desktop GNB (visible, order 1). Previously `.first` picked mobile
    and click timed out. Now visible desktop wins.
    """
    from zero_touch_qa.locator_resolver import LocatorResolver

    resolver = LocatorResolver(dual_match_page)
    loc = resolver.resolve("role=link, name=회사소개")
    assert loc is not None
    assert loc.is_visible()
    where = loc.get_attribute("data-where")
    assert where == "desktop", f"hidden mobile drawer was selected: where={where}"


def test_visibility_healer_swaps_via_filter_visible(dual_match_page, make_executor):
    """T-H (C) — `.filter(visible=True)` filters multi-match to visible only and swaps.

    When a multi-match locator landed on a hidden first, the healer's
    _find_visible_sibling swaps it for a visible element with the same
    base. This is the safety net for non-role+name paths (text/CSS).
    """
    executor = make_executor()
    raw = dual_match_page.get_by_role("link", name="회사소개")
    # raw is multi-match (count=2). first is hidden mobile, second is visible desktop.
    assert raw.count() == 2
    assert not raw.first.is_visible()

    swap = executor._find_visible_sibling(raw, step_id=42)
    assert swap is not None
    assert swap.is_visible()
    assert swap.get_attribute("data-where") == "desktop"


# ── T-H (D)(E)(F) — page-level activator + size poll ──────────────────────


@pytest.fixture
def lazy_menu_page(_ctx):
    """Lazy-rendered fixture: GNB has height:0 right after load.
    Once any mouseover hits the body, .activated is added and the menu opens."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "lazy_menu.html").as_uri())
    yield p
    p.close()


def test_lazy_menu_link_initially_hidden(lazy_menu_page):
    """Baseline — right after load, GNB links have height:0 and are hidden."""
    loc = lazy_menu_page.get_by_role("link", name="회사소개")
    # role query catches height:0, but is_visible returns False.
    assert loc.count() == 1
    # box height is 0, so is_visible is False.
    assert not loc.first.is_visible()


def test_visibility_healer_activates_lazy_menu_via_page_hover(
    lazy_menu_page, make_executor,
):
    """T-H (E) — page-level activator probe triggers body mouseover so
    the .activated class is added, the menu unfolds, and the link becomes visible."""
    executor = make_executor()
    loc = lazy_menu_page.get_by_role("link", name="회사소개").first
    assert not loc.is_visible()

    # healer goes through D / ancestor / E stages and tries to make it visible
    swap = executor._heal_visibility(lazy_menu_page, loc, step_id=42)

    # swap is None (single match, no sibling swap), but loc itself must be visible.
    assert swap is None
    assert loc.is_visible(), "lazy menu didn't open even after page-level hover"


# ── T-H (H) — multi-level cascade hover ──────────────────────────────────


@pytest.fixture
def cascade_menu_page(_ctx):
    """3-level cascade hover menu fixture — 회사소개 > 회사연혁 > ~2013 pattern."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "cascade_menu.html").as_uri())
    yield p
    p.close()


def test_cascade_menu_leaf_initially_hidden(cascade_menu_page):
    """Baseline — the 3-level leaf link `~2013` is hidden before hover."""
    leaf = cascade_menu_page.locator("#link-2013")
    assert leaf.count() == 1
    assert not leaf.first.is_visible()


def test_cascade_menu_single_hover_insufficient(cascade_menu_page):
    """Baseline — a single-stage hover (the leaf's direct parent) is not enough.

    Confirms this case is not solvable with the healer's single-ancestor hover today.
    """
    leaf = cascade_menu_page.locator("#link-2013").first
    # only hover the direct parent li (activates level-3 only)
    direct_parent_li = cascade_menu_page.locator("#lvl2-history").first
    try:
        direct_parent_li.hover(timeout=1500)
    except Exception:
        pass
    cascade_menu_page.wait_for_timeout(200)
    # outer level-1 isn't hovered, so submenu-2 stays closed → leaf stays hidden
    assert not leaf.is_visible(), "if a single hover unwinds the cascade, the regression is meaningless"


def test_visibility_healer_cascade_unlocks_three_level_menu(
    cascade_menu_page, make_executor,
):
    """T-H (H) — cascade hover (outermost → innermost) reaches a 3-level leaf.

    The healer reverses the candidates so it hovers from the outermost
    inward → each level's :hover cascade activates and the leaf link
    becomes visible.
    """
    executor = make_executor()
    leaf = cascade_menu_page.locator("#link-2013").first
    assert not leaf.is_visible()

    swap = executor._heal_visibility(cascade_menu_page, leaf, step_id=77)

    # single match, so swap is None. The leaf itself must be visible after cascade.
    assert swap is None
    assert leaf.is_visible(), "3-level leaf still not unfolded after cascade hover"


