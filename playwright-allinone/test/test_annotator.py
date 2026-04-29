"""TR.7+ — codegen .py 의 hover 자동 주입 (annotator) 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

from recording_service.annotator import annotate_script


def _make_script(tmp_path: Path, body: str) -> Path:
    """codegen 표준 출력 구조로 .py 작성."""
    p = tmp_path / "original.py"
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


def test_annotate_inserts_hover_for_nav_chain(tmp_path: Path) -> None:
    """nav#gnb chain 안의 click 앞에 hover 라인 자동 삽입."""
    src = _make_script(
        tmp_path,
        "page.locator('nav#gnb').locator('li').filter(has_text='회사소개').get_by_role('link', name='About').click()",
    )
    dst = tmp_path / "original_annotated.py"

    res = annotate_script(str(src), str(dst))

    assert res.injected == 1
    assert res.examined_clicks == 1
    annotated = dst.read_text(encoding="utf-8")
    # hover 라인이 click 라인 직전에 있어야 함.
    lines = annotated.splitlines()
    hover_idx = next(i for i, l in enumerate(lines) if ".hover()" in l)
    click_idx = next(i for i, l in enumerate(lines) if ".click()" in l)
    assert hover_idx < click_idx
    assert "nav#gnb" in lines[hover_idx]
    # auto-annotated 마커 주석 포함.
    assert "auto-annotated" in lines[hover_idx]


def test_annotate_no_change_for_simple_clicks(tmp_path: Path) -> None:
    """nav/menu/dropdown 신호 없는 click 은 그대로 (회귀 0)."""
    src = _make_script(
        tmp_path,
        "page.get_by_role('button', name='Submit').click()\n"
        "page.locator('div.card').get_by_role('button', name='Confirm').click()",
    )
    dst = tmp_path / "original_annotated.py"

    res = annotate_script(str(src), str(dst))

    assert res.injected == 0
    assert res.examined_clicks == 2
    # source 가 그대로여야 함 (개행만 일치).
    assert src.read_text() == dst.read_text()


def test_annotate_handles_multiple_clicks(tmp_path: Path) -> None:
    """여러 click 중 일부만 hover 주입 — 라인 인덱스가 밀리지 않는지 확인."""
    src = _make_script(
        tmp_path,
        "page.get_by_role('button', name='Login').click()\n"
        "page.locator('nav#gnb').get_by_role('link', name='회사소개').click()\n"
        "page.get_by_role('button', name='Logout').click()",
    )
    dst = tmp_path / "original_annotated.py"

    res = annotate_script(str(src), str(dst))

    assert res.injected == 1
    assert res.examined_clicks == 3
    # syntax check — 결과 파일이 valid python 이어야 함.
    import ast as _ast
    _ast.parse(dst.read_text(encoding="utf-8"))


def test_annotate_dropdown_class_is_picked(tmp_path: Path) -> None:
    src = _make_script(
        tmp_path,
        "page.locator('.dropdown').get_by_role('link', name='Logout').click()",
    )
    dst = tmp_path / "original_annotated.py"

    res = annotate_script(str(src), str(dst))

    assert res.injected == 1
    assert ".dropdown" in res.triggers[0]


def test_annotate_returns_existing_path_when_no_clicks(tmp_path: Path) -> None:
    """click 자체가 없으면 injected=0, 출력은 입력과 동일."""
    src = _make_script(tmp_path, "page.goto('https://example.com')")
    dst = tmp_path / "original_annotated.py"

    res = annotate_script(str(src), str(dst))

    assert res.injected == 0
    assert res.examined_clicks == 0
    assert src.read_text() == dst.read_text()
