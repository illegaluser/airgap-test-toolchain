"""T-H — Visibility Healer 단위 테스트.

codegen 이 hover-then-click sequence 의 hover 를 빠뜨려 element 가 hidden 상태로
click 시도되는 케이스를 ancestor hover 로 자동 복구하는지 검증.
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
    """베이스라인 — submenu 의 link 는 hover 전에는 visible 하지 않다."""
    assert not page.locator("#link-about").is_visible()


def test_visibility_healer_makes_hidden_link_clickable(page, make_executor):
    """visibility healer 가 li:hover 트리거 후 hidden link 를 visible 로 만든다."""
    executor = make_executor()
    loc = page.locator("#link-about").first
    assert not loc.is_visible()

    executor._heal_visibility(page, loc, step_id=99)

    # hover 후 visible 이어야 함.
    assert loc.is_visible()


def test_visibility_healer_noop_when_already_visible(page, make_executor):
    """이미 visible 한 element 에는 healer 가 부수 효과 0 — 그대로 visible 유지."""
    executor = make_executor()
    loc = page.locator("#menu-company").first  # nav 최상단 — 항상 visible
    assert loc.is_visible()
    executor._heal_visibility(page, loc, step_id=99)
    assert loc.is_visible()


# ── T-H (B + C) — 다중 매치 + 모바일/데스크탑 dual rendering ────────────────


@pytest.fixture
def dual_match_page(_ctx):
    """모바일 드로어(hidden) + 데스크탑 GNB(visible) 둘 다 같은 라벨을 가진 fixture."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "dual_match_menu.html").as_uri())
    yield p
    p.close()


def test_resolver_prefers_visible_match_over_hidden(dual_match_page):
    """T-H (B) — resolver 가 role+name 매치 시 visible 매치를 우선 선택.

    fixture: 모바일 드로어(transform:scale(0), DOM 순서 0) + 데스크탑 GNB(visible, 순서 1).
    이전엔 `.first` 가 mobile 을 잡아 click timeout. 이제 visible 한 desktop 선택.
    """
    from zero_touch_qa.locator_resolver import LocatorResolver

    resolver = LocatorResolver(dual_match_page)
    loc = resolver.resolve("role=link, name=회사소개")
    assert loc is not None
    assert loc.is_visible()
    where = loc.get_attribute("data-where")
    assert where == "desktop", f"hidden 모바일 드로어가 선택됨: where={where}"


def test_visibility_healer_swaps_via_filter_visible(dual_match_page, make_executor):
    """T-H (C) — `.filter(visible=True)` 로 다중 매치에서 visible 만 추려 swap.

    multi-match locator 가 hidden first 를 잡았을 때 healer 의 _find_visible_sibling
    이 동일 base 의 visible 한 element 로 교체. role+name 외 경로 (text/CSS) 의
    안전망.
    """
    executor = make_executor()
    raw = dual_match_page.get_by_role("link", name="회사소개")
    # raw 는 multi-match locator (count=2). first 는 hidden mobile, second 는 visible desktop.
    assert raw.count() == 2
    assert not raw.first.is_visible()

    swap = executor._find_visible_sibling(raw, step_id=42)
    assert swap is not None
    assert swap.is_visible()
    assert swap.get_attribute("data-where") == "desktop"


# ── T-H (D)(E)(F) — page-level activator + size poll ──────────────────────


@pytest.fixture
def lazy_menu_page(_ctx):
    """페이지 로드 직후 GNB 가 height:0 인 lazy-rendered fixture.
    body 에 한 번이라도 mouseover 가 들어오면 .activated 가 추가되어 menu 펼침."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "lazy_menu.html").as_uri())
    yield p
    p.close()


def test_lazy_menu_link_initially_hidden(lazy_menu_page):
    """베이스라인 — 페이지 로드 직후 GNB link 는 height:0 으로 hidden."""
    loc = lazy_menu_page.get_by_role("link", name="회사소개")
    # role query 는 height:0 도 잡지만 is_visible 은 False 를 반환.
    assert loc.count() == 1
    # 박스 높이가 0 이라 is_visible False.
    assert not loc.first.is_visible()


def test_visibility_healer_activates_lazy_menu_via_page_hover(
    lazy_menu_page, make_executor,
):
    """T-H (E) — page-level activator probe 가 body mouseover 를 trigger 해
    .activated 클래스를 부여 → menu 펼치고 link 가 visible 됨."""
    executor = make_executor()
    loc = lazy_menu_page.get_by_role("link", name="회사소개").first
    assert not loc.is_visible()

    # healer 가 D / ancestor / E 단계 거쳐 visible 화 시도
    swap = executor._heal_visibility(lazy_menu_page, loc, step_id=42)

    # swap 은 None (단일 매치라 형제 swap 안 됨), 하지만 loc 자체가 visible 되어야 함.
    assert swap is None
    assert loc.is_visible(), "page-level hover 후에도 lazy menu 가 펼쳐지지 않음"


# ── T-H (H) — multi-level cascade hover ──────────────────────────────────


@pytest.fixture
def cascade_menu_page(_ctx):
    """3-level cascade hover 메뉴 fixture — 회사소개 > 회사연혁 > ~2013 패턴."""
    p = _ctx.new_page()
    p.goto((FIXTURES_DIR / "cascade_menu.html").as_uri())
    yield p
    p.close()


def test_cascade_menu_leaf_initially_hidden(cascade_menu_page):
    """베이스라인 — 3-level 의 leaf link `~2013` 은 hover 전에 hidden."""
    leaf = cascade_menu_page.locator("#link-2013")
    assert leaf.count() == 1
    assert not leaf.first.is_visible()


def test_cascade_menu_single_hover_insufficient(cascade_menu_page):
    """베이스라인 — 단 1단계 hover (leaf 의 직계 부모) 만으론 leaf 가 visible 안 됨.

    현재 healer 의 단일 ancestor hover 로는 풀 수 없는 케이스임을 보증.
    """
    leaf = cascade_menu_page.locator("#link-2013").first
    # 직계 부모 li 만 hover (level-3 만 활성)
    direct_parent_li = cascade_menu_page.locator("#lvl2-history").first
    try:
        direct_parent_li.hover(timeout=1500)
    except Exception:
        pass
    cascade_menu_page.wait_for_timeout(200)
    # outer level-1 이 hover 안 되어 submenu-2 가 열리지 않음 → leaf 도 hidden
    assert not leaf.is_visible(), "단일 hover 만으로 cascade 가 풀리면 cascade 회귀 의미 없음"


def test_visibility_healer_cascade_unlocks_three_level_menu(
    cascade_menu_page, make_executor,
):
    """T-H (H) — cascade hover (outermost → innermost) 로 3-level 메뉴 leaf 도달.

    healer 가 candidates 를 reverse 후 outermost 부터 누적 hover →
    각 level 의 :hover cascade 가 활성화되어 leaf link 가 visible 됨.
    """
    executor = make_executor()
    leaf = cascade_menu_page.locator("#link-2013").first
    assert not leaf.is_visible()

    swap = executor._heal_visibility(cascade_menu_page, leaf, step_id=77)

    # 단일 매치라 swap 은 None. leaf 자체가 cascade 후 visible 되어야 함.
    assert swap is None
    assert leaf.is_visible(), "cascade hover 후에도 3-level leaf 가 펼쳐지지 않음"


