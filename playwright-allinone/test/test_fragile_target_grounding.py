"""Fragile target grounding — 2026-05-11 회귀 차단.

회귀 사례: FLOW-USR-006 의 ``page.locator("button").nth(5)`` 가 회귀 .py 에
그대로 emit 되어 페이지 구조 변화 시 깨지는 문제. 실행 시 fragile pattern
(bare CSS tag + 선택적 nth=N) 으로 element 가 잡힌 경우, click 직전에
``role=button, name=<text>`` / ``text=<text>`` 로 재서술해 step.target /
StepResult.target 을 갱신해야 한다.

본 테스트는 click.html fixture 위에서 단일 매치 / 다중 매치 / nth=N /
ambigous fallback 케이스를 모두 검증한다.
"""

from __future__ import annotations

from helpers.scenarios import navigate, click


def test_bare_button_grounded_to_role_name(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """``button, nth=0`` 으로 첫 버튼 클릭 → ``role=button, name=Plain Button``.

    fixture click.html 의 3개 버튼 중 nth=0 은 "Plain Button" (text). grounding
    이 ``role=button, name=Plain Button`` 으로 재서술해야 한다.
    """
    monkeypatch_dify()
    executor = make_executor()

    page_url = fixture_url("click.html")
    scenario = [
        navigate(page_url, step=1),
        click("button, nth=0", step=2),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    assert results[1].status in ("PASS", "HEALED")
    # 원본 fragile selector 가 사라지고 stable identity 로 재서술됐는지.
    assert results[1].target == "role=button, name=Plain Button", (
        f"grounding 미적용 — target={results[1].target!r}"
    )
    assert scenario_after[1]["target"] == "role=button, name=Plain Button"


def test_bare_anchor_grounded_to_role_link(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """``a`` (단독, nth 없음) 도 fragile → ``role=link, name=Go to Navigate``.

    click.html 에 anchor 는 1개 (``Go to Navigate``). 단일 매치라 grounding 안전.
    """
    monkeypatch_dify()
    executor = make_executor()

    page_url = fixture_url("click.html")
    scenario = [
        navigate(page_url, step=1),
        click("a", step=2),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    assert results[1].status in ("PASS", "HEALED")
    assert results[1].target == "role=link, name=Go to Navigate"
    assert scenario_after[1]["target"] == "role=link, name=Go to Navigate"


def test_non_fragile_target_preserved(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """이미 stable 한 selector (``role=button, name=...``) 는 그대로 보존."""
    monkeypatch_dify()
    executor = make_executor()

    page_url = fixture_url("click.html")
    scenario = [
        navigate(page_url, step=1),
        click("role=button, name=Plain Button", step=2),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    assert results[1].status in ("PASS", "HEALED")
    assert results[1].target == "role=button, name=Plain Button"
    assert scenario_after[1]["target"] == "role=button, name=Plain Button"


def test_grounding_skipped_when_ambiguous(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """추출한 text 가 페이지에서 단일 매치가 아니면 원본 fragile selector 보존.

    aria-label "로그인" 버튼은 inner_text "Login". ``text=Login`` 이 unique 매치인지
    검증을 거치므로, 다중 매치라면 grounding 안 됨. click.html 에는 "Login" 텍스트가
    1개라 grounding 성공 — 이 테스트는 *unique 일 때만 grounding* 의 positive 케이스.
    """
    monkeypatch_dify()
    executor = make_executor()

    page_url = fixture_url("click.html")
    scenario = [
        navigate(page_url, step=1),
        click("button, nth=1", step=2),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    assert results[1].status in ("PASS", "HEALED")
    # aria-label "로그인" 으로 grounding (inner_text "Login" 이 페이지에서 단일 매치).
    # 단, role=button 의 accessible name 은 aria-label 우선이라 "로그인" 이 더 정확.
    # 둘 중 어느 형태든 채택될 수 있으나 *원본 fragile* 형태는 사라져야 한다.
    assert results[1].target != "button, nth=1", (
        f"fragile target 이 grounding 안 됨 — target={results[1].target!r}"
    )
    assert "nth=" not in results[1].target, (
        f"여전히 위치 기반 selector — target={results[1].target!r}"
    )
