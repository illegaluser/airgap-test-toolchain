"""Dynamic annotator (실 페이지 visibility probe + ancestor hover) 단위 테스트.

7dc61c99e8f9 회귀 — 정적 분석으론 잡을 수 없는 dropdown / aria-haspopup
케이스에서 hover 라인을 정확히 prepend 하는지 검증. 픽스처는
``test/fixtures/dropdown_menu.html`` (file:// URL 로 navigate).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from recording_service.annotator import (
    AnnotateResult,
    _extract_replay_actions,
    annotate_script_dynamic,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
DROPDOWN_URL = (FIXTURES_DIR / "dropdown_menu.html").as_uri()


def _write_codegen(tmp_path: Path, body_lines: list[str]) -> Path:
    """codegen 형식의 .py — `def run(playwright)` body 안에 ``body_lines`` 를
    동일 들여쓰기(4 space) 로 삽입해 작성."""
    src = tmp_path / "original.py"
    indent = "    "
    body = "\n".join(f"{indent}{line}" for line in body_lines)
    src.write_text(
        "from playwright.sync_api import sync_playwright\n\n\n"
        "def run(playwright):\n"
        f"{indent}browser = playwright.chromium.launch()\n"
        f"{indent}context = browser.new_context()\n"
        f"{indent}page = context.new_page()\n"
        f"{indent}page.goto({DROPDOWN_URL!r})\n"
        f"{body}\n"
        f"{indent}context.close()\n"
        f"{indent}browser.close()\n\n\n"
        "with sync_playwright() as playwright:\n"
        f"{indent}run(playwright)\n",
        encoding="utf-8",
    )
    return src


# ─────────────────────────────────────────────────────────────────────────
# AST 추출 — replay 가능한 액션이 정확히 잡히는지
# ─────────────────────────────────────────────────────────────────────────


def test_extract_replay_actions_picks_supported(tmp_path: Path) -> None:
    """navigate / click / fill / press 만 추출, 그 외는 무시."""
    import ast

    src = _write_codegen(tmp_path, [
        'page.get_by_role("button", name="apply-mgr-btn").click()',
        'page.get_by_role("textbox", name="키워드").fill("API")',
        'page.get_by_role("textbox", name="키워드").press("Enter")',
        'page.wait_for_timeout(100)',
    ])
    tree = ast.parse(src.read_text(encoding="utf-8"))
    actions = _extract_replay_actions(tree)
    kinds = [a.kind for a in actions]
    # navigate + click + fill + press = 4. wait 는 dispatch_action 에 없어 skip.
    assert "navigate" in kinds
    assert "click" in kinds
    assert "fill" in kinds
    assert "press" in kinds


# ─────────────────────────────────────────────────────────────────────────
# Dynamic probe — hidden dropdown 의 ancestor hover 자동 식별
# ─────────────────────────────────────────────────────────────────────────


def test_dynamic_dropdown_hover_prepended(tmp_path: Path) -> None:
    """dropdown 안 hidden 버튼 click → :hover-css 트리거 발견 + hover 라인 prepend."""
    src = _write_codegen(tmp_path, [
        'page.get_by_role("button", name="사용신청 관리").click()',
    ])
    dst = tmp_path / "original_annotated.py"
    res = annotate_script_dynamic(str(src), str(dst), headless=True)

    assert res.examined_clicks == 1
    assert res.injected == 1, f"hover 1개 prepend 기대, 실제 {res.injected}\nsrc:\n{src.read_text()}\ndst:\n{dst.read_text()}\ntriggers={res.triggers}"
    out = dst.read_text(encoding="utf-8")
    # click 직전에 hover 라인이 있어야 함
    assert ".hover()" in out
    assert "auto-annotated (dynamic)" in out


def test_dynamic_aria_haspopup_hover_prepended(tmp_path: Path) -> None:
    """aria-haspopup ancestor 안 hidden 버튼 click → aria-haspopup 트리거 발견."""
    src = _write_codegen(tmp_path, [
        'page.get_by_role("button", name="로그아웃").click()',
    ])
    dst = tmp_path / "original_annotated.py"
    res = annotate_script_dynamic(str(src), str(dst), headless=True)

    assert res.examined_clicks == 1
    assert res.injected == 1
    assert any("aria-haspopup" in t or "data-state" in t or ":hover-css" in t for t in res.triggers), \
        f"aria 계열 trigger 기대, 실제 {res.triggers}"


def test_dynamic_visible_button_no_hover(tmp_path: Path) -> None:
    """이미 visible 한 버튼 click → hover 주입 0 (false-positive 방지)."""
    src = _write_codegen(tmp_path, [
        'page.get_by_role("button", name="바로 클릭").click()',
    ])
    dst = tmp_path / "original_annotated.py"
    res = annotate_script_dynamic(str(src), str(dst), headless=True)

    assert res.examined_clicks == 1
    assert res.injected == 0
    # dst 가 src 와 동일 (hover prepend 안 됨)
    assert ".hover()" not in dst.read_text(encoding="utf-8")


def test_dynamic_multiple_clicks_correct_lines(tmp_path: Path) -> None:
    """여러 click 중 일부만 hover — 라인 인덱스가 밀리지 않는지."""
    src = _write_codegen(tmp_path, [
        'page.get_by_role("button", name="사용신청 관리").click()',
        'page.get_by_role("button", name="바로 클릭").click()',
        'page.get_by_role("button", name="서비스 문의").click()',
    ])
    dst = tmp_path / "original_annotated.py"
    res = annotate_script_dynamic(str(src), str(dst), headless=True)

    assert res.examined_clicks == 3
    # 사용신청 관리 + 서비스 문의 둘 다 dropdown → 2 hover prepend.
    assert res.injected == 2
    out = dst.read_text(encoding="utf-8")
    # hover 가 click 직전에 잘 prepend 됐는지 — 사용신청 관리 click 보다
    # 한 줄 위에 hover 가 있어야.
    lines = out.splitlines()
    apply_idx = next(i for i, l in enumerate(lines) if "사용신청 관리" in l and "click()" in l)
    assert ".hover()" in lines[apply_idx - 1], f"사용신청 관리 click 직전 라인:\n{lines[apply_idx-2:apply_idx+1]}"
