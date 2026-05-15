"""SPA 라우트 / 비동기 DOM 마운트 회귀 가드 — 클릭 후 N 초 뒤에 등장하는
element 를 다음 step 이 곧장 찾으려 했을 때.

사용자 실측 (2026-05-15): koreaconnect 포털 step 14 click '확인' (사용신청
완료 팝업) 직후 step 15 click '사용종료' 가 'role=button, name=사용종료'
resolver 0건 → Dify LLM 치유 → returncode 143 으로 실패. 이는 step 14 의
설ле 윈도 (3s+2s) 가 지났음에도 step 15 의 element 가 아직 DOM 에 마운트
되지 않은 SPA 비동기 렌더 패턴.

본 슈트는:
1) 픽스처가 정말로 4 초 지연 마운트를 만들어내는지 베이스라인.
2) executor 가 resolver 0건을 만났을 때, 곧장 LLM 으로 가지 않고 짧은
   polling-wait 으로 element 등장을 기다린 후 회복하는지 확인.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from helpers.scenarios import click, navigate


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_URL = (FIXTURES_DIR / "delayed_appear_button.html").as_uri()


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
    p.goto(FIXTURE_URL)
    yield p
    p.close()


def test_baseline_step2_button_not_present_initially(page):
    """베이스라인 — 페이지 로드 직후 사용종료 버튼은 DOM 에 없다."""
    assert page.get_by_role("button", name="사용종료").count() == 0
    assert page.get_by_text("사용종료").count() == 0


def test_baseline_step2_button_appears_after_4s(page):
    """베이스라인 — step 1 트리거 후 ~4초 뒤에 사용종료 버튼이 등장."""
    page.locator("#btn-step1").click()
    # 4 초 + 약간의 여유.
    page.wait_for_selector("#btn-step2", state="attached", timeout=6000)
    assert page.get_by_role("button", name="사용종료").count() == 1


def test_executor_waits_for_delayed_element_before_llm_healing(
    make_executor, run_scenario,
):
    """회귀 가드 — step 2 가 '사용종료' resolver 0건 → 짧은 polling-wait →
    element 등장 후 PASS. LLM 치유로 직행하면 안 됨.

    실패 시: executor 의 _try_initial_target 이 resolver None 을 받자마자
    return None 하고 caller 가 LLM 으로 분기하는 회귀."""
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        navigate(FIXTURE_URL, step=1),
        click("role=button, name=트리거 (step 1)", step=2),
        click("role=button, name=사용종료", step=3),
    ])
    statuses = [r.status for r in results]
    assert statuses[0] == "PASS", f"navigate 실패: {results[0]}"
    assert statuses[1] in ("PASS", "HEALED"), f"step 1 트리거 실패: {results[1]}"
    assert statuses[2] in ("PASS", "HEALED"), (
        f"step 2 ('사용종료' delayed-appear) 실패: {results[2]}\n"
        f"executor 가 resolver 0건 직후 LLM 으로 직행했을 가능성 — "
        f"polling-wait fallback 필요."
    )
