"""T-A (P0.4) — converter_ast unit tests.

Design: docs/PLAN_PRODUCTION_READINESS.md §"T-A — converter AST migration"

Coverage:
- 8 corpus fixtures (popup chain / .nth / .first / .filter / nested
  locator / frame_locator / expect / 14-DSL specials) match
  expected.json exactly
- _split_modifiers / _apply_modifiers unit behavior
- non-standard patterns → CodegenAstError or line fallback
- AST + line fallback yield the same result through the integrated
  wrapper (within the corpus)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zero_touch_qa.converter import convert_playwright_to_dsl
from zero_touch_qa.converter_ast import (
    CodegenAstError,
    _AstConverter,
    convert_via_ast,
)


CORPUS_DIR = Path(__file__).parent / "fixtures" / "codegen_corpus"
CORPUS_PATTERNS = [
    "01_simple",
    "02_popup_chain",
    "03_nth",
    "04_first",
    "05_filter",
    "06_nested_locator",
    "07_frame_locator",
    "08_expect_and_specials",
]


# ─────────────────────────────────────────────────────────────────────────
# Corpus accuracy — all 8 patterns match expected exactly
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", CORPUS_PATTERNS)
def test_corpus_via_ast_matches_expected(pattern: str, tmp_path: Path) -> None:
    """convert_via_ast (called directly) matches the corpus expected.json exactly."""
    src = CORPUS_DIR / f"{pattern}.py"
    expected_path = CORPUS_DIR / f"{pattern}.expected.json"
    actual = convert_via_ast(str(src), str(tmp_path))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"{pattern}: {len(actual)} steps vs expected {len(expected)} steps"
    )


@pytest.mark.parametrize("pattern", CORPUS_PATTERNS)
def test_corpus_via_wrapper_matches_expected(
    pattern: str, tmp_path: Path,
) -> None:
    """convert_playwright_to_dsl wrapper (AST-first + line fallback) yields the same result."""
    src = CORPUS_DIR / f"{pattern}.py"
    expected_path = CORPUS_DIR / f"{pattern}.expected.json"
    actual = convert_playwright_to_dsl(str(src), str(tmp_path))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected


# ─────────────────────────────────────────────────────────────────────────
# Popup chain — preserve all 6/6 steps from this session's naver case
# ─────────────────────────────────────────────────────────────────────────


def test_popup_chain_preserves_6_steps(tmp_path: Path) -> None:
    """No drops in 02_popup_chain (naver scenario) — both popup actions preserved."""
    src = CORPUS_DIR / "02_popup_chain.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert len(actual) == 6
    # both actions inside the popup (page1.click(엔터), page1.click(기사 헤드라인)) preserved
    targets = [s["target"] for s in actual]
    assert "role=link, name=엔터" in targets
    assert "role=link, name=기사 헤드라인" in targets


# ─────────────────────────────────────────────────────────────────────────
# Modifier preservation — .nth / .first / .filter
# ─────────────────────────────────────────────────────────────────────────


def test_nth_modifier_preserved(tmp_path: Path) -> None:
    """``.nth(N)`` is preserved as a trailing ``, nth=N`` on target."""
    src = CORPUS_DIR / "03_nth.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"] == "role=link, name=Read more, nth=0"
    assert actual[2]["target"] == "role=link, name=Read more, nth=2"


def test_first_modifier_preserved(tmp_path: Path) -> None:
    """``.first`` becomes ``, nth=0``."""
    src = CORPUS_DIR / "04_first.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"].endswith(", nth=0")


def test_filter_has_text_preserved(tmp_path: Path) -> None:
    """``.filter(has_text=...)`` is preserved as ``, has_text=...``."""
    src = CORPUS_DIR / "05_filter.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"] == "role=listitem, has_text=Premium"
    # the second action in 05 is .filter + .first combined → both modifiers preserved
    assert actual[2]["target"] == "a, has_text=구매하기, nth=0"


# ─────────────────────────────────────────────────────────────────────────
# Frame locator chain preservation (prerequisite for T-C)
# ─────────────────────────────────────────────────────────────────────────


def test_frame_locator_chain_preserved(tmp_path: Path) -> None:
    """``page.frame_locator(sel).get_by_role(...)`` becomes ``frame=sel >> role=...``."""
    src = CORPUS_DIR / "07_frame_locator.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"].startswith("frame=#payment-iframe >> ")
    assert "role=textbox, name=Card number" in actual[1]["target"]
    assert actual[2]["target"].startswith("frame=#payment-iframe >> ")


# ─────────────────────────────────────────────────────────────────────────
# Nested locator
# ─────────────────────────────────────────────────────────────────────────


def test_nested_locator_chain(tmp_path: Path) -> None:
    """``page.locator(parent).locator(child)`` flattens to ``parent >> child``."""
    src = CORPUS_DIR / "06_nested_locator.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"] == "#sidebar >> role=button, name=Settings"
    assert actual[2]["target"] == ".card >> button.confirm"


# ─────────────────────────────────────────────────────────────────────────
# Expect (verify) conversion
# ─────────────────────────────────────────────────────────────────────────


def test_expect_to_have_text(tmp_path: Path) -> None:
    src = CORPUS_DIR / "08_expect_and_specials.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    verify_steps = [s for s in actual if s["action"] == "verify"]
    assert any(
        s["target"] == "h1" and s["value"] == "Welcome" for s in verify_steps
    )


def test_expect_to_be_visible(tmp_path: Path) -> None:
    src = CORPUS_DIR / "08_expect_and_specials.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    verify_steps = [s for s in actual if s["action"] == "verify"]
    assert any(
        s["target"] == "role=button, name=Submit" and s["value"] == ""
        for s in verify_steps
    )


# ─────────────────────────────────────────────────────────────────────────
# 14-DSL specials (upload / drag / scroll / mock_*)
# ─────────────────────────────────────────────────────────────────────────


def test_special_actions_all_present(tmp_path: Path) -> None:
    """All 5 actions (upload / drag / scroll / mock_status / mock_data) convert."""
    src = CORPUS_DIR / "08_expect_and_specials.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    actions = {s["action"] for s in actual}
    assert {"upload", "drag", "scroll", "mock_status", "mock_data"}.issubset(actions)


# ─────────────────────────────────────────────────────────────────────────
# _split_modifiers — unit-function behavior
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "target,expected_base,expected_modifiers",
    [
        # base only
        ("role=link, name=뉴스", "role=link, name=뉴스", []),
        # nth modifier
        ("role=link, name=뉴스, nth=1", "role=link, name=뉴스", [("nth", "1")]),
        # has_text modifier
        ("role=listitem, has_text=Premium", "role=listitem", [("has_text", "Premium")]),
        # combined (filter + first)
        (
            "a, has_text=구매하기, nth=0",
            "a",
            [("has_text", "구매하기"), ("nth", "0")],
        ),
        # CSS only
        ("#sidebar >> button", "#sidebar >> button", []),
        # CSS + modifier
        ("a[href=\"/x\"], nth=0", "a[href=\"/x\"]", [("nth", "0")]),
        # the ',' inside name is not a modifier (name= is not a modifier key)
        ("role=link, name=A, B", "role=link, name=A, B", []),
    ],
)
def test_split_modifiers_unit(
    target: str, expected_base: str, expected_modifiers: list,
) -> None:
    from zero_touch_qa.locator_resolver import _split_modifiers
    base, modifiers = _split_modifiers(target)
    assert base == expected_base
    assert modifiers == expected_modifiers


# ─────────────────────────────────────────────────────────────────────────
# Non-standard / abnormal patterns
# ─────────────────────────────────────────────────────────────────────────


def test_syntax_error_raises_codegen_ast_error(tmp_path: Path) -> None:
    """Invalid Python syntax → CodegenAstError → signal for the wrapper to use line fallback."""
    bad = tmp_path / "bad.py"
    bad.write_text("def run(playwright):\n    page = context.new_page()\n    page.goto(", encoding="utf-8")
    with pytest.raises(CodegenAstError):
        convert_via_ast(str(bad), str(tmp_path))


def test_no_run_function_returns_empty(tmp_path: Path) -> None:
    """File without `def run` → empty scenario (wrapper retries via line fallback)."""
    no_run = tmp_path / "no_run.py"
    no_run.write_text(
        "from playwright.sync_api import sync_playwright\nprint('hi')\n",
        encoding="utf-8",
    )
    result = convert_via_ast(str(no_run), str(tmp_path))
    assert result == []


def test_wrapper_falls_back_when_ast_returns_empty(tmp_path: Path) -> None:
    """When AST returns empty, the wrapper retries with line fallback.

    For legacy codegen output without ``def run``, line-based parsing
    still picks up page.goto and friends.
    """
    legacy = tmp_path / "legacy_no_run.py"
    legacy.write_text(
        "from playwright.sync_api import sync_playwright\n\n"
        "page.goto(\"https://example.com/\")\n"
        "page.get_by_role(\"button\", name=\"OK\").click()\n",
        encoding="utf-8",
    )
    actual = convert_playwright_to_dsl(str(legacy), str(tmp_path))
    assert len(actual) >= 1  # line fallback at least catches navigate
    assert actual[0]["action"] == "navigate"


def test_unknown_method_returns_none_step(tmp_path: Path) -> None:
    """Methods codegen never emits (e.g. page.foo()) are simply skipped."""
    src = tmp_path / "unknown.py"
    src.write_text(
        "from playwright.sync_api import Playwright, sync_playwright\n\n"
        "def run(playwright: Playwright) -> None:\n"
        "    page = browser.new_page()\n"
        "    page.goto(\"https://x\")\n"
        "    page.unknown_method()\n",
        encoding="utf-8",
    )
    actual = convert_via_ast(str(src), str(tmp_path))
    # only navigate (1 step) — unknown_method is skipped
    assert len(actual) == 1
    assert actual[0]["action"] == "navigate"


# ─────────────────────────────────────────────────────────────────────────
# Step numbers / fallback_targets defaults
# ─────────────────────────────────────────────────────────────────────────


def test_step_numbers_are_sequential(tmp_path: Path) -> None:
    src = CORPUS_DIR / "01_simple.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert [s["step"] for s in actual] == list(range(1, len(actual) + 1))


def test_fallback_targets_default_empty_list(tmp_path: Path) -> None:
    src = CORPUS_DIR / "01_simple.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert all(s["fallback_targets"] == [] for s in actual)


# ─────────────────────────────────────────────────────────────────────────
# Page variable scope — popup_info → promote page var
# ─────────────────────────────────────────────────────────────────────────


def test_page_var_promoted_after_popup_info_value(tmp_path: Path) -> None:
    """After ``page1 = page1_info.value``, actions on page1 are also handled."""
    src = CORPUS_DIR / "02_popup_chain.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    # 4 = page.click(뉴스홈), 5 = page1.click(엔터), 6 = page1.click(기사 헤드라인)
    assert actual[4]["target"] == "role=link, name=엔터"
    assert actual[5]["target"] == "role=link, name=기사 헤드라인"


def test_page_close_action_skipped() -> None:
    """Close calls like ``page2.close()`` are skipped (no action mapping)."""
    converter = _AstConverter()
    # call _convert_call_to_step directly — test-friendly form
    import ast
    tree = ast.parse("page.close()")
    call = tree.body[0].value
    assert converter._convert_call_to_step(call) is None


# ─────────────────────────────────────────────────────────────────────────
# scenario.json file output
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_json_written_to_output_dir(tmp_path: Path) -> None:
    src = CORPUS_DIR / "01_simple.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    out = tmp_path / "scenario.json"
    assert out.exists()
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == actual


# ─────────────────────────────────────────────────────────────────────────
# File not found
# ─────────────────────────────────────────────────────────────────────────


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        convert_via_ast(str(tmp_path / "nope.py"), str(tmp_path))


# ─────────────────────────────────────────────────────────────────────────
# T-H tie-in — converter statically identifies a hover-trigger ancestor
# and auto-inserts a hover step before the click (conservative heuristic)
# ─────────────────────────────────────────────────────────────────────────


def _make_codegen_script(tmp_path: Path, body: str) -> Path:
    """Write a .py in codegen's standard output shape — body lives inside `def run(playwright)`."""
    p = tmp_path / "rec.py"
    src = (
        "from playwright.sync_api import Playwright, sync_playwright\n"
        "\n"
        "def run(playwright: Playwright) -> None:\n"
        "    browser = playwright.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
        + "    context.close()\n    browser.close()\n"
        + "\nwith sync_playwright() as p:\n    run(p)\n"
    )
    p.write_text(src, encoding="utf-8")
    return p


def test_hover_prepended_for_nav_chain(tmp_path: Path) -> None:
    """If the chain has a nav ancestor, auto-insert a hover step before the click."""
    src = _make_codegen_script(
        tmp_path,
        "page.locator('nav#gnb').locator('li').filter(has_text='회사소개').get_by_role('link', name='About').click()",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert "hover" in actions
    hover_idx = actions.index("hover")
    click_idx = actions.index("click")
    assert hover_idx < click_idx
    assert "nav#gnb" in steps[hover_idx]["target"]


    """Chains without nav/menu/dropdown signal don't get a hover (avoids false positives)."""
    src = _make_codegen_script(
        tmp_path,
        "page.get_by_role('button', name='Submit').click()\n"
        "page.locator('div.card').get_by_role('button', name='Confirm').click()",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert "hover" not in actions


def test_hover_prepended_for_dropdown_class(tmp_path: Path) -> None:
    src = _make_codegen_script(
        tmp_path,
        "page.locator('.dropdown').get_by_role('link', name='Logout').click()",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert actions[0] == "hover"
    assert ".dropdown" in steps[0]["target"]
