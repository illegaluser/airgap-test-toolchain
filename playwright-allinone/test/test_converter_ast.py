"""T-A (P0.4) — converter_ast 단위 테스트.

설계: PLAN_PRODUCTION_READINESS.md §"T-A — converter AST 화"

검증 영역:
- 8 corpus fixture (popup chain / .nth / .first / .filter / nested locator /
  frame_locator / expect / 14-DSL 특수 액션) → expected.json 정확 일치
- _split_modifiers / _apply_modifiers 의 단위 동작
- 비표준 패턴 → CodegenAstError 또는 line fallback
- AST + line fallback 이 통합 wrapper 에서 같은 결과 (corpus 한정)
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
# Corpus 정확도 — 8 패턴 모두 expected 와 정확 일치
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", CORPUS_PATTERNS)
def test_corpus_via_ast_matches_expected(pattern: str, tmp_path: Path) -> None:
    """convert_via_ast 가 corpus 의 expected.json 과 정확 일치 (직접 호출)."""
    src = CORPUS_DIR / f"{pattern}.py"
    expected_path = CORPUS_DIR / f"{pattern}.expected.json"
    actual = convert_via_ast(str(src), str(tmp_path))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"{pattern}: {len(actual)} 스텝 vs expected {len(expected)} 스텝"
    )


@pytest.mark.parametrize("pattern", CORPUS_PATTERNS)
def test_corpus_via_wrapper_matches_expected(
    pattern: str, tmp_path: Path,
) -> None:
    """convert_playwright_to_dsl wrapper (AST 우선 + line fallback) 도 동일 결과."""
    src = CORPUS_DIR / f"{pattern}.py"
    expected_path = CORPUS_DIR / f"{pattern}.expected.json"
    actual = convert_playwright_to_dsl(str(src), str(tmp_path))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected


# ─────────────────────────────────────────────────────────────────────────
# Popup chain — 이번 세션 naver 케이스 6/6 스텝 보존
# ─────────────────────────────────────────────────────────────────────────


def test_popup_chain_preserves_6_steps(tmp_path: Path) -> None:
    """02_popup_chain (naver 시나리오) 의 popup 액션 2 개 누락 없음."""
    src = CORPUS_DIR / "02_popup_chain.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert len(actual) == 6
    # popup 안의 두 액션 (page1.click(엔터), page1.click(기사 헤드라인)) 보존
    targets = [s["target"] for s in actual]
    assert "role=link, name=엔터" in targets
    assert "role=link, name=기사 헤드라인" in targets


# ─────────────────────────────────────────────────────────────────────────
# Modifier 보존 — .nth / .first / .filter
# ─────────────────────────────────────────────────────────────────────────


def test_nth_modifier_preserved(tmp_path: Path) -> None:
    """``.nth(N)`` 가 target 의 후미 ``, nth=N`` 으로 보존."""
    src = CORPUS_DIR / "03_nth.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"] == "role=link, name=Read more, nth=0"
    assert actual[2]["target"] == "role=link, name=Read more, nth=2"


def test_first_modifier_preserved(tmp_path: Path) -> None:
    """``.first`` 가 ``, nth=0`` 으로 변환."""
    src = CORPUS_DIR / "04_first.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"].endswith(", nth=0")


def test_filter_has_text_preserved(tmp_path: Path) -> None:
    """``.filter(has_text=...)`` 가 ``, has_text=...`` 으로 보존."""
    src = CORPUS_DIR / "05_filter.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"] == "role=listitem, has_text=Premium"
    # 05 의 두번째 액션은 .filter + .first 조합 → 두 modifier 모두 보존
    assert actual[2]["target"] == "a, has_text=구매하기, nth=0"


# ─────────────────────────────────────────────────────────────────────────
# Frame locator chain 보존 (T-C 의 전제)
# ─────────────────────────────────────────────────────────────────────────


def test_frame_locator_chain_preserved(tmp_path: Path) -> None:
    """``page.frame_locator(sel).get_by_role(...)`` 가 ``frame=sel >> role=...`` 으로 보존."""
    src = CORPUS_DIR / "07_frame_locator.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"].startswith("frame=#payment-iframe >> ")
    assert "role=textbox, name=Card number" in actual[1]["target"]
    assert actual[2]["target"].startswith("frame=#payment-iframe >> ")


# ─────────────────────────────────────────────────────────────────────────
# Nested locator
# ─────────────────────────────────────────────────────────────────────────


def test_nested_locator_chain(tmp_path: Path) -> None:
    """``page.locator(parent).locator(child)`` 가 ``parent >> child`` 로 평탄화."""
    src = CORPUS_DIR / "06_nested_locator.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert actual[1]["target"] == "#sidebar >> role=button, name=Settings"
    assert actual[2]["target"] == ".card >> button.confirm"


# ─────────────────────────────────────────────────────────────────────────
# Expect (verify) 변환
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
# 14-DSL 특수 액션 (upload / drag / scroll / mock_*)
# ─────────────────────────────────────────────────────────────────────────


def test_special_actions_all_present(tmp_path: Path) -> None:
    """upload / drag / scroll / mock_status / mock_data 5 액션 모두 변환."""
    src = CORPUS_DIR / "08_expect_and_specials.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    actions = {s["action"] for s in actual}
    assert {"upload", "drag", "scroll", "mock_status", "mock_data"}.issubset(actions)


# ─────────────────────────────────────────────────────────────────────────
# _split_modifiers — 단위 함수 동작
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
        # name 안의 ',' 는 modifier 가 아님 (name= 은 modifier 키 아님)
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
# 비표준 / 비정상 패턴
# ─────────────────────────────────────────────────────────────────────────


def test_syntax_error_raises_codegen_ast_error(tmp_path: Path) -> None:
    """잘못된 Python 구문은 CodegenAstError → wrapper 가 line fallback 하는 신호."""
    bad = tmp_path / "bad.py"
    bad.write_text("def run(playwright):\n    page = context.new_page()\n    page.goto(", encoding="utf-8")
    with pytest.raises(CodegenAstError):
        convert_via_ast(str(bad), str(tmp_path))


def test_no_run_function_returns_empty(tmp_path: Path) -> None:
    """`def run` 이 없는 파일 → 빈 시나리오 (wrapper 가 line fallback 시도)."""
    no_run = tmp_path / "no_run.py"
    no_run.write_text(
        "from playwright.sync_api import sync_playwright\nprint('hi')\n",
        encoding="utf-8",
    )
    result = convert_via_ast(str(no_run), str(tmp_path))
    assert result == []


def test_wrapper_falls_back_when_ast_returns_empty(tmp_path: Path) -> None:
    """AST 가 빈 결과를 주면 wrapper 가 line fallback 으로 재시도.

    ``def run`` 없는 legacy codegen 출력에서도 line 기반은 page.goto 등을 잡는다.
    """
    legacy = tmp_path / "legacy_no_run.py"
    legacy.write_text(
        "from playwright.sync_api import sync_playwright\n\n"
        "page.goto(\"https://example.com/\")\n"
        "page.get_by_role(\"button\", name=\"OK\").click()\n",
        encoding="utf-8",
    )
    actual = convert_playwright_to_dsl(str(legacy), str(tmp_path))
    assert len(actual) >= 1  # line fallback 이 적어도 navigate 는 잡음
    assert actual[0]["action"] == "navigate"


def test_unknown_method_returns_none_step(tmp_path: Path) -> None:
    """codegen 이 만들지 않는 메서드 (예: page.foo()) 는 단순히 스킵."""
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
    # navigate 만 1 스텝 — unknown_method 는 스킵
    assert len(actual) == 1
    assert actual[0]["action"] == "navigate"


# ─────────────────────────────────────────────────────────────────────────
# Step 번호 / fallback_targets 기본값
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
# Page 변수 스코프 — popup_info → page var 승격
# ─────────────────────────────────────────────────────────────────────────


def test_page_var_promoted_after_popup_info_value(tmp_path: Path) -> None:
    """``page1 = page1_info.value`` 후 page1 의 액션도 처리되는지 확인."""
    src = CORPUS_DIR / "02_popup_chain.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    # 4 = page.click(뉴스홈), 5 = page1.click(엔터), 6 = page1.click(기사 헤드라인)
    assert actual[4]["target"] == "role=link, name=엔터"
    assert actual[5]["target"] == "role=link, name=기사 헤드라인"


def test_page_close_action_skipped() -> None:
    """``page2.close()`` 등 close 호출은 스킵 (액션 매핑 없음)."""
    converter = _AstConverter()
    # 직접 _convert_call_to_step 호출 — 테스트 친화 형태
    import ast
    tree = ast.parse("page.close()")
    call = tree.body[0].value
    assert converter._convert_call_to_step(call) is None


# ─────────────────────────────────────────────────────────────────────────
# scenario.json 파일 출력
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
# T-H 연계 — converter 가 hover-trigger ancestor 를 정적으로 식별해 click
# 앞에 hover step 을 자동 삽입 (보수적 휴리스틱)
# ─────────────────────────────────────────────────────────────────────────


def _make_codegen_script(tmp_path: Path, body: str) -> Path:
    """codegen 표준 출력 구조로 .py 작성 — `def run(playwright)` 안에 body 삽입."""
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
    """chain 안에 nav ancestor 가 있으면 click 앞에 hover step 자동 삽입."""
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


def test_no_hover_prepended_for_simple_click(tmp_path: Path) -> None:
    """nav/menu/dropdown 신호 없는 chain 은 hover 삽입 안 함 (false-positive 방지)."""
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
