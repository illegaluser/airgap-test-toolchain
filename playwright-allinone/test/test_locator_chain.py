"""LocatorResolver chain 해석 (P0.1 #2) 통합 회귀.

AST 변환기(T-A) 가 emit 하는 nested locator chain — `#sidebar >> role=button,
name=Settings`, `frame=#x >> role=textbox, name=Card number`, `.card >>
button.confirm` — 가 executor 의 시나리오 경로에서 정확히 누적 chain 으로
풀려 의도한 element 만 클릭되는지 검증.

본 모듈은 fixture HTML + ``QAExecutor`` 통합 패턴을 사용한다 (`make_executor`
/ `run_scenario` / `fixture_url`). 이전엔 자체 `with sync_playwright()` fixture
를 썼으나 다른 테스트와 같은 process 에서 돌 때 asyncio loop 충돌이 발생해
운영 부담이 컸다. executor 가 LocatorResolver 를 그대로 거치므로 검증 영역은
보존된다 — chain 이 안 풀리면 잘못된 element 가 클릭되어 #last-clicked 텍스트
가 기대값과 달라져 verify 가 실패한다.
"""

from __future__ import annotations

from helpers.scenarios import click, navigate, verify


def _verify_last_clicked(expected: str, *, step: int):
    return verify(
        "#last-clicked", step=step,
        condition="contains_text", value=expected,
    )


def test_chain_css_then_role(make_executor, run_scenario, fixture_url):
    """`#sidebar >> role=button, name=Settings` — sidebar 안의 Settings 만 매치.
    main 안에도 같은 텍스트가 있으므로 chain 이 안 풀리면 잘못된 element 클릭."""
    executor = make_executor()
    page = fixture_url("nested_locator.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click("#sidebar >> role=button, name=Settings", step=2),
        _verify_last_clicked("sidebar:Settings", step=3),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_chain_css_then_css(make_executor, run_scenario, fixture_url):
    """`.card >> button.confirm` — 첫 .card 의 confirm 버튼."""
    executor = make_executor()
    page = fixture_url("nested_locator.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click(".card >> button.confirm", step=2),
        _verify_last_clicked("card:Confirm", step=3),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_chain_css_then_testid(make_executor, run_scenario, fixture_url):
    """`#sidebar >> testid=sidebar-help` — sidebar 의 testid element 만 매치."""
    executor = make_executor()
    page = fixture_url("nested_locator.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click("#sidebar >> testid=sidebar-help", step=2),
        _verify_last_clicked("sidebar:Help", step=3),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_chain_with_modifier_nth(make_executor, run_scenario, fixture_url):
    """`.card >> button.confirm, nth=1` — 두 번째 .card 의 confirm 버튼."""
    executor = make_executor()
    page = fixture_url("nested_locator.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click(".card >> button.confirm, nth=1", step=2),
        _verify_last_clicked("card:Confirm-2", step=3),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_chain_with_modifier_has_text(make_executor, run_scenario, fixture_url):
    """`.card >> button, has_text=Cancel` — has_text 로 좁힌 매칭."""
    executor = make_executor()
    page = fixture_url("nested_locator.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click(".card >> button, has_text=Cancel", step=2),
        _verify_last_clicked("card:Cancel", step=3),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_chain_frame_locator_textbox(make_executor, run_scenario, fixture_url):
    """`frame=#payment-iframe >> role=textbox, name=Card number` — iframe 내부
    input fill + value verify (frame chain 누적이 정상이어야)."""
    executor = make_executor()
    page = fixture_url("nested_locator.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        # fill 은 nested_locator fixture 의 iframe input 에 입력.
        # chain 안 풀리면 textbox 매치 실패 → fill timeout.
        {
            "step": 2, "action": "fill",
            "target": "frame=#payment-iframe >> role=textbox, name=Card number",
            "value": "1111", "description": "iframe Card number",
            "fallback_targets": [],
        },
        verify(
            "frame=#payment-iframe >> role=textbox, name=Card number", step=3,
            condition="value", value="1111",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"
