"""T-A (P0.4) — converter_ast 단위 테스트.

설계: docs/PLAN_PRODUCTION_READINESS.md §"T-A — converter AST 화"

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
    _dedupe_consecutive_clicks,
    _normalized_click_identity,
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
    "09_title_alt",
    "10_popup_then_back_to_main",
    "11_exact_match",
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
# Page identity 메타 — popup chain 의 page var 가 step 메타로 전달
# ─────────────────────────────────────────────────────────────────────────


def test_popup_chain_page_identity_metadata(tmp_path: Path) -> None:
    """02_popup_chain — 각 step 의 page 메타 + popup 트리거의 popup_to 정확."""
    src = CORPUS_DIR / "02_popup_chain.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    # page 변천: page → page → page → (popup trigger) → page1 → (popup trigger)
    assert [s.get("page") for s in actual] == [
        "page", "page", "page", "page", "page1", "page1",
    ]
    # popup 트리거 step (step 4: page→page1, step 6: page1→page2) 만 popup_to 보유
    assert actual[3].get("popup_to") == "page1"
    assert actual[5].get("popup_to") == "page2"
    # 그 외 step 은 popup_to 없음
    for i in (0, 1, 2, 4):
        assert "popup_to" not in actual[i], f"step {i+1} 에 popup_to 가 없어야 함"


def test_popup_then_back_to_main_keeps_original_page(tmp_path: Path) -> None:
    """10_popup_then_back_to_main — popup 떴어도 후속 액션은 원본 page 유지.

    5e1e5a6f141a 케이스 재현. step 2 가 popup 트리거 (popup_to=page1) 지만
    step 3-5 는 page="page" — executor 가 자동전환 안 하고 원본 유지해야 함.
    """
    src = CORPUS_DIR / "10_popup_then_back_to_main.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    assert [s.get("page") for s in actual] == ["page"] * 5
    assert actual[1].get("popup_to") == "page1"
    for i in (0, 2, 3, 4):
        assert "popup_to" not in actual[i]


def test_exact_kwarg_preserved_in_target(tmp_path: Path) -> None:
    """``get_by_role(..., exact=True)`` 가 target 에 ``, exact=true`` 로 보존.

    5e1e5a6f1 케이스 — substring 매칭으로 ``"API"`` 가 ``"오픈API"`` 에 잘못
    잡히던 회귀를 막기 위함.
    """
    src = CORPUS_DIR / "11_exact_match.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    # exact=True click → target 끝에 exact=true
    assert actual[1]["target"] == "role=button, name=API, exact=true"
    # exact 미사용 click → target 에 exact 없음
    assert actual[2]["target"] == "role=link, name=제출"


def test_internal_popup_marker_stripped(tmp_path: Path) -> None:
    """``_pending_popup_info`` internal marker 는 scenario.json 에 절대 노출 안 됨."""
    src = CORPUS_DIR / "02_popup_chain.py"
    actual = convert_via_ast(str(src), str(tmp_path))
    # 직렬화된 scenario.json 도 동일
    written = json.loads((tmp_path / "scenario.json").read_text(encoding="utf-8"))
    for s in actual + written:
        for k in s.keys():
            assert not k.startswith("_"), f"internal key '{k}' leaked: {s}"


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


# ─────────────────────────────────────────────────────────────────────────
# assert → verify step 매핑 (tour 스크립트의 codegen 패턴 통합)
# ─────────────────────────────────────────────────────────────────────────


def test_assert_url_not_contains_maps_to_verify_step(tmp_path: Path) -> None:
    """``assert "X" not in page.url`` → verify(url_not_contains)."""
    src = _make_codegen_script(
        tmp_path,
        "page.goto('https://x.test/p')\n"
        "assert \"errorMsg\" not in page.url",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert actions == ["navigate", "verify"], actions
    assert steps[1]["condition"] == "url_not_contains"
    # target 은 placeholder('body') — executor 의 url_* 분기는 locator 안 쓰지만
    # main step flow 의 resolve 단계가 valid selector 를 요구.
    assert steps[1]["target"] == "body"
    assert steps[1]["value"] == "errorMsg"


def test_assert_url_contains_maps_to_verify_step(tmp_path: Path) -> None:
    """``assert "X" in page.url`` → verify(url_contains)."""
    src = _make_codegen_script(
        tmp_path,
        "page.goto('https://x.test/search?q=foo')\n"
        "assert \"q=foo\" in page.url",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert actions == ["navigate", "verify"]
    assert steps[1]["condition"] == "url_contains"
    assert steps[1]["value"] == "q=foo"


def test_assert_inner_text_min_length_maps_to_verify_step(tmp_path: Path) -> None:
    """``assert len(page.inner_text("body")) >= N`` → verify(min_text_length)."""
    src = _make_codegen_script(
        tmp_path,
        "page.goto('https://x.test/p')\n"
        "assert len(page.inner_text(\"body\")) >= 50",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert actions == ["navigate", "verify"]
    assert steps[1]["condition"] == "min_text_length"
    assert steps[1]["target"] == "body"
    assert steps[1]["value"] == "50"


def test_unrelated_assert_is_ignored(tmp_path: Path) -> None:
    """매칭 안 되는 assert (예: ``assert page.title()``) 는 무시 — 시나리오에 안 들어감.

    실 스크립트 실행에서는 그대로 raise 되므로 검증 의미는 보존.
    """
    src = _make_codegen_script(
        tmp_path,
        "page.goto('https://x.test/p')\n"
        "assert page.title()",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert actions == ["navigate"], "untranslated assert 가 verify 로 잘못 잡힘"


def test_tour_codegen_pattern_full_extraction(tmp_path: Path) -> None:
    """tour 스크립트 전체 패턴 (URL 당 navigate + 2 verify) 정합 검증."""
    src = _make_codegen_script(
        tmp_path,
        "page.goto('https://x.test/a')\n"
        "assert \"errorMsg\" not in page.url\n"
        "assert len(page.inner_text(\"body\")) >= 50\n"
        "page.goto('https://x.test/b')\n"
        "assert \"errorMsg\" not in page.url\n"
        "assert len(page.inner_text(\"body\")) >= 50",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    assert len(steps) == 6
    assert [s["action"] for s in steps] == [
        "navigate", "verify", "verify", "navigate", "verify", "verify",
    ]


def test_try_except_body_is_recursed_for_extraction(tmp_path: Path) -> None:
    """tour 스크립트가 각 URL 을 try/except 로 감싸도 변환기가 body 의 step 을 추출.

    회귀 가드: 사용자가 받은 tour 가 첫 URL fail 에서 abort 되지 않게 try/except
    로 감싸는데, 이때 변환기가 try.body 안의 navigate/assert 를 못 보면 시나리오가
    비게 된다.
    """
    src = _make_codegen_script(
        tmp_path,
        "try:\n"
        "    page.goto('https://x.test/a')\n"
        "    assert \"errorMsg\" not in page.url\n"
        "    assert len(page.inner_text(\"body\")) >= 50\n"
        "except AssertionError:\n"
        "    pass\n"
        "try:\n"
        "    page.goto('https://x.test/b')\n"
        "    assert \"errorMsg\" not in page.url\n"
        "except AssertionError:\n"
        "    pass",
    )
    steps = convert_via_ast(str(src), str(tmp_path))
    actions = [s["action"] for s in steps]
    assert actions == [
        "navigate", "verify", "verify",  # a: navigate + 2 verify
        "navigate", "verify",            # b: navigate + 1 verify
    ], f"unexpected: {actions}"


# ─────────────────────────────────────────────────────────────────────────
# 중복 click 압축 — wrapper button + inner link 같은 codegen 이중 emit 회피
# ─────────────────────────────────────────────────────────────────────────


def test_normalized_click_identity_collapses_repeated_tokens():
    step = {"action": "click", "target": "role=button, name=페르소나 ChatBot 페르소나 ChatBot"}
    assert _normalized_click_identity(step) == "name=페르소나 ChatBot"


def test_normalized_click_identity_strips_exact_qualifier():
    a = {"action": "click", "target": "role=link, name=Foo, exact=true"}
    b = {"action": "click", "target": "role=link, name=Foo"}
    assert _normalized_click_identity(a) == _normalized_click_identity(b)


def test_normalized_click_identity_returns_none_for_non_click():
    assert _normalized_click_identity({"action": "fill", "target": "role=textbox, name=q"}) is None


def test_dedupe_keeps_popup_to_carrier():
    """outer button + inner link 가 같은 카드를 가리키면 popup_to 보유한 쪽 보존."""
    s = [
        {"step": 1, "action": "navigate", "target": "", "value": "http://x", "page": "page"},
        {"step": 2, "action": "click", "target": "role=button, name=Card Card", "page": "page"},
        {"step": 3, "action": "click", "target": "role=link, name=Card", "page": "page", "popup_to": "page1"},
        {"step": 4, "action": "click", "target": "role=button, name=Other", "page": "page1"},
    ]
    out = _dedupe_consecutive_clicks(s)
    assert len(out) == 3
    assert out[1]["popup_to"] == "page1"
    # step 번호 재부여
    assert [x["step"] for x in out] == [1, 2, 3]


def test_dedupe_no_change_for_distinct_names():
    s = [
        {"step": 1, "action": "click", "target": "role=button, name=A", "page": "page"},
        {"step": 2, "action": "click", "target": "role=button, name=B", "page": "page"},
    ]
    out = _dedupe_consecutive_clicks(s)
    assert len(out) == 2


def test_dedupe_no_change_across_pages():
    """같은 name 이라도 page 가 다르면 다른 click — 압축 안 함."""
    s = [
        {"step": 1, "action": "click", "target": "role=button, name=A", "page": "page"},
        {"step": 2, "action": "click", "target": "role=button, name=A", "page": "page1"},
    ]
    out = _dedupe_consecutive_clicks(s)
    assert len(out) == 2


def test_dedupe_does_not_touch_fill_repeats():
    """fill 반복은 의도적일 수 있어 손대지 않는다 (CapsLock 토글 등)."""
    s = [
        {"step": 1, "action": "fill", "target": "role=textbox, name=q", "value": "a", "page": "page"},
        {"step": 2, "action": "fill", "target": "role=textbox, name=q", "value": "ab", "page": "page"},
    ]
    out = _dedupe_consecutive_clicks(s)
    assert len(out) == 2


# ─────────────────────────────────────────────────────────────────────────
# IME 노이즈 필터 — CapsLock / Unidentified / 빈 fill
# ─────────────────────────────────────────────────────────────────────────


def test_strip_ime_noise_drops_unidentified_press():
    """press 'Unidentified' 는 Playwright 가 Unknown key 로 거부 — drop."""
    from zero_touch_qa.converter_ast import _strip_ime_noise
    s = [
        {"step": 1, "action": "click", "target": "x", "page": "page"},
        {"step": 2, "action": "press", "target": "y", "value": "Unidentified", "page": "page"},
        {"step": 3, "action": "fill", "target": "y", "value": "버스", "page": "page"},
    ]
    out = _strip_ime_noise(s)
    actions = [(o["action"], o.get("value", "")) for o in out]
    assert ("press", "Unidentified") not in actions
    assert len(out) == 2


def test_strip_ime_noise_drops_capslock_press():
    """press CapsLock 은 재생 시 IME 상태와 무관 — drop."""
    from zero_touch_qa.converter_ast import _strip_ime_noise
    s = [
        {"step": 1, "action": "press", "target": "x", "value": "CapsLock", "page": "page"},
        {"step": 2, "action": "fill", "target": "x", "value": "MCP", "page": "page"},
    ]
    out = _strip_ime_noise(s)
    assert len(out) == 1
    assert out[0]["action"] == "fill"
    assert out[0]["step"] == 1


def test_strip_ime_noise_drops_empty_fill_when_next_is_fill():
    """빈 fill + 다음에 같은 target 의 non-empty fill → 빈 fill drop."""
    from zero_touch_qa.converter_ast import _strip_ime_noise
    s = [
        {"step": 1, "action": "fill", "target": "role=textbox, name=q", "value": "", "page": "page"},
        {"step": 2, "action": "fill", "target": "role=textbox, name=q", "value": "버스", "page": "page"},
    ]
    out = _strip_ime_noise(s)
    assert len(out) == 1
    assert out[0]["value"] == "버스"


def test_strip_ime_noise_keeps_empty_fill_when_no_followup():
    """빈 fill 만 있고 후속 fill 없으면 보존 (validator 별도 처리)."""
    from zero_touch_qa.converter_ast import _strip_ime_noise
    s = [
        {"step": 1, "action": "fill", "target": "x", "value": "", "page": "page"},
        {"step": 2, "action": "click", "target": "y", "page": "page"},
    ]
    out = _strip_ime_noise(s)
    assert len(out) == 2


def test_strip_ime_noise_keeps_empty_fill_for_different_target():
    """다음 fill 의 target 이 다르면 빈 fill 보존."""
    from zero_touch_qa.converter_ast import _strip_ime_noise
    s = [
        {"step": 1, "action": "fill", "target": "role=textbox, name=a", "value": "", "page": "page"},
        {"step": 2, "action": "fill", "target": "role=textbox, name=b", "value": "X", "page": "page"},
    ]
    out = _strip_ime_noise(s)
    assert len(out) == 2


def test_press_sequentially_converts_to_fill_action(tmp_path):
    """``press_sequentially("값", delay=80)`` 호출도 ``fill`` action 으로 변환.

    2026-05-11 FLOW-USR-007 회귀 — 이전 회귀 .py 가 fill 대신 press_sequentially
    로 emit 된 형태였는데 converter 가 인식 못 해 typing 단계가 시나리오에서
    통째로 소실. 빈 fill 만 남고 자동완성 트리거 실패.
    """
    src = tmp_path / "regression.py"
    src.write_text(
        "from playwright.sync_api import Playwright, sync_playwright\n"
        "def run(playwright: Playwright) -> None:\n"
        "    browser = playwright.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        "    page.goto('https://example.test')\n"
        "    page.get_by_role('textbox', name='Q').first.click()\n"
        "    page.get_by_role('textbox', name='Q').first.fill('')\n"
        "    page.get_by_role('textbox', name='Q').first.press_sequentially('hello', delay=80)\n"
        "    page.get_by_role('button', name='hello world').first.click()\n"
        "with sync_playwright() as playwright:\n"
        "    run(playwright)\n",
        encoding="utf-8",
    )
    out = convert_via_ast(str(src), str(tmp_path))
    actions_values = [(s["action"], s.get("value", "")) for s in out]
    # 빈 fill 은 strip_ime_noise 가 drop, press_sequentially("hello") 가 fill step 으로 등장.
    assert ("fill", "hello") in actions_values, (
        f"press_sequentially 가 fill action 으로 변환 안 됨: {actions_values}"
    )
    # 빈 fill 은 drop 됨 (직후 non-empty fill 있어서).
    assert ("fill", "") not in actions_values, (
        f"빈 fill 이 IME noise drop 안 됨: {actions_values}"
    )


def test_line_fallback_converts_press_sequentially_to_fill(tmp_path):
    """line 기반 fallback 도 press_sequentially 인식 (AST 실패 시 안전망)."""
    from zero_touch_qa.converter import _convert_via_lines
    src = tmp_path / "regression.py"
    src.write_text(
        "page.goto('https://example.test')\n"
        "page.get_by_role('textbox', name='Q').first.press_sequentially('hello', delay=80)\n",
        encoding="utf-8",
    )
    out = _convert_via_lines(str(src), str(tmp_path))
    fills = [s for s in out if s["action"] == "fill"]
    assert len(fills) >= 1, f"line fallback 이 press_sequentially 를 fill 로 변환 안 함: {out}"
    assert fills[0]["value"] == "hello"


def test_strip_ime_noise_combined_d13ea6c9320c_pattern():
    """실제 d13ea6c9320c 케이스 — click → 빈 fill → CapsLock → Unidentified → 'fill 버스'.
    → click → fill 버스 만 남음.
    """
    from zero_touch_qa.converter_ast import _strip_ime_noise
    s = [
        {"step": 22, "action": "click", "target": "role=textbox, name=디지털자원명 입력", "page": "page"},
        {"step": 23, "action": "fill", "target": "role=textbox, name=디지털자원명 입력", "value": "", "page": "page"},
        {"step": 24, "action": "press", "target": "role=textbox, name=디지털자원명 입력", "value": "CapsLock", "page": "page"},
        {"step": 25, "action": "press", "target": "role=textbox, name=디지털자원명 입력", "value": "Unidentified", "page": "page"},
        {"step": 26, "action": "fill", "target": "role=textbox, name=디지털자원명 입력", "value": "버스", "page": "page"},
    ]
    out = _strip_ime_noise(s)
    assert [o["action"] for o in out] == ["click", "fill"]
    assert out[1]["value"] == "버스"
    assert [o["step"] for o in out] == [1, 2]


# ──────────────────────────────────────────────────────────────────────────
# bare iframe chain 정규화 — codegen 이 ``page.locator("iframe[...] >> ...")``
# 한 호출 안에 frame entry 를 ``>>`` 합성 selector 로 emit 한 경우, AST
# 변환기가 frame entry segment 를 ``frame=`` prefix 로 끌어내야 한다
# (resolver/healer 양쪽이 frame scope 에 진입할 수 있게).
# 2026-05-15 SmartEditor 회귀 사고의 단위 회귀.
# ──────────────────────────────────────────────────────────────────────────


def test_stabilize_iframe_title_stable_pass_through():
    """짧고 안정적인 title 은 그대로 유지 — 약화하면 ambiguity 가 늘어난다."""
    from zero_touch_qa.converter_ast import _stabilize_iframe_title
    assert _stabilize_iframe_title('iframe[title="에디터 전체 영역"]') == \
        'iframe[title="에디터 전체 영역"]'


def test_stabilize_iframe_title_dynamic_long_to_prefix():
    """동적 신호 (긴 길이 + ``:`` + 연속 공백) → ``[title^="<prefix>"]`` 약화."""
    from zero_touch_qa.converter_ast import _stabilize_iframe_title
    sel = (
        'iframe[title="편집 모드 영역 -  - CTRL+2:첫 번째 툴바, '
        'CTRL+3:두 번째 툴바, CTRL+4:편집 영역"]'
    )
    out = _stabilize_iframe_title(sel)
    # 안정 prefix 인 "편집 모드 영역" 까지만 남기고 prefix matcher 로 약화.
    assert out.startswith('iframe[title^=')
    assert "편집 모드 영역" in out
    # 동적 부분 (``CTRL+2:`` 등) 은 제거돼 있어야 한다.
    assert "CTRL" not in out


def test_split_iframe_chain_splits_frame_entries_and_leaf():
    """``iframe[...] >> iframe[...] >> #leaf`` 가 frames + leaf 로 분리된다."""
    from zero_touch_qa.converter_ast import _split_iframe_chain
    frames, leaf = _split_iframe_chain(
        'iframe[title="outer"] >> iframe[title="inner"] >> #child',
    )
    assert frames == ['iframe[title="outer"]', 'iframe[title="inner"]']
    assert leaf == "#child"


def test_split_iframe_chain_no_iframe_returns_empty_frames():
    """frame entry 가 하나도 없으면 frames=[] + leaf 는 원본 그대로."""
    from zero_touch_qa.converter_ast import _split_iframe_chain
    frames, leaf = _split_iframe_chain("#a >> .b > c")
    assert frames == []
    assert leaf == "#a >> .b > c"


def test_convert_ast_locator_chain_with_iframe_normalizes_to_frame_prefix(tmp_path):
    """``page.locator("iframe[...] >> #card").fill(...)`` →
    ``frame=iframe[...]`` 가 chain prefix 로 끌려나오고 leaf 가 target 의 본체.
    """
    src_path = tmp_path / "src.py"
    src_path.write_text(
        "from playwright.sync_api import sync_playwright\n"
        "def run(playwright):\n"
        "    browser = playwright.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        "    page.goto('https://example.com')\n"
        "    page.locator('iframe[title=\"에디터 전체 영역\"] >> #keditor_body').click()\n"
        "with sync_playwright() as playwright:\n"
        "    run(playwright)\n",
        encoding="utf-8",
    )
    steps = convert_via_ast(str(src_path), str(tmp_path))
    click_step = next(s for s in steps if s.get("action") == "click")
    target = click_step["target"]
    assert target.startswith('frame=iframe[title="에디터 전체 영역"] >> ')
    assert target.endswith("#keditor_body")


def test_convert_ast_locator_chain_nested_iframes_normalize_each(tmp_path):
    """nested iframe 두 단계 모두 ``frame=`` 으로 정규화 + 동적 title 약화."""
    src_path = tmp_path / "src.py"
    src_path.write_text(
        "from playwright.sync_api import sync_playwright\n"
        "def run(playwright):\n"
        "    browser = playwright.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        "    page.goto('https://example.com')\n"
        "    page.locator("
        "'iframe[title=\"에디터 전체 영역\"] >> "
        "iframe[title=\"편집 모드 영역 -  - CTRL+2:첫 번째 툴바\"] >> "
        "#keditor_body').click()\n"
        "with sync_playwright() as playwright:\n"
        "    run(playwright)\n",
        encoding="utf-8",
    )
    steps = convert_via_ast(str(src_path), str(tmp_path))
    click_step = next(s for s in steps if s.get("action") == "click")
    target = click_step["target"]
    # 외곽 — 짧고 안정 → 원본 유지.
    assert 'frame=iframe[title="에디터 전체 영역"]' in target
    # 내부 — 동적 신호 → prefix matcher 로 약화.
    assert 'frame=iframe[title^=' in target
    assert "편집 모드 영역" in target
    # 단축키 안내 등 동적 부분은 사라져야 한다.
    assert "CTRL" not in target
    # leaf 는 그대로.
    assert target.endswith("#keditor_body")
