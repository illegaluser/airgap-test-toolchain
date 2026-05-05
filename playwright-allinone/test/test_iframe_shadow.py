"""T-C (P0.2) — iframe / open shadow / closed shadow 통합 회귀.

검증 영역:
- 단일 iframe: fill + click + verify (frame chain 정상 풀림)
- iframe → 부모 postMessage 가 부모 #parent-status 갱신
- nested iframe (2단 깊이) click + verify
- open shadow Web Component: fill + submit + verify
- closed shadow target: 즉시 FAIL (자동화 불가능 신호 — hang 없이)

본 모듈은 fixture HTML + ``QAExecutor`` 통합 패턴을 사용한다 (`make_executor` /
`run_scenario` / `fixture_url`). 이전엔 자체 `with sync_playwright()` fixture
를 썼으나 다른 테스트와 같은 process 에서 돌 때 asyncio loop 충돌이 발생해
운영 부담이 컸다. executor 의 시나리오 실행 경로가 동일한 LocatorResolver 를
거치므로 검증 영역은 보존된다.
"""

from __future__ import annotations

from helpers.scenarios import click, fill, navigate, verify, wait


def test_iframe_payment_card_input(make_executor, run_scenario, fixture_url):
    """iframe 안 input 에 값 입력 → 같은 input 의 value 검증."""
    executor = make_executor()
    page = fixture_url("iframe_payment.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        fill("frame=#payment-iframe >> #card", "4242424242424242", step=2),
        verify(
            "frame=#payment-iframe >> #card", step=3,
            condition="value", value="4242424242424242",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_iframe_pay_button_approves(make_executor, run_scenario, fixture_url):
    """iframe 안 버튼 클릭 → 같은 iframe 의 #result 가 'approved'."""
    executor = make_executor()
    page = fixture_url("iframe_payment.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        fill("frame=#payment-iframe >> #card", "4242424242424242", step=2),
        click("frame=#payment-iframe >> role=button, name=Pay", step=3),
        verify(
            "frame=#payment-iframe >> #result", step=4,
            condition="contains_text", value="approved",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_iframe_postmessage_to_parent(make_executor, run_scenario, fixture_url):
    """iframe 의 postMessage 가 부모의 #parent-status 를 'parent:received' 로 갱신."""
    executor = make_executor()
    page = fixture_url("iframe_payment.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        fill("frame=#payment-iframe >> #card", "4242424242424242", step=2),
        click("frame=#payment-iframe >> role=button, name=Pay", step=3),
        wait(300, step=4, description="postMessage 처리 대기"),
        verify(
            "#parent-status", step=5,
            condition="contains_text", value="parent:received",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_nested_iframe_deep_click(make_executor, run_scenario, fixture_url):
    """outer → inner 2단 iframe 깊이의 버튼 클릭 + 결과 verify."""
    executor = make_executor()
    page = fixture_url("iframe_nested.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click(
            "frame=#outer-frame >> frame=#inner-frame >> #deep-btn", step=2,
        ),
        verify(
            "frame=#outer-frame >> frame=#inner-frame >> #deep-result", step=3,
            condition="contains_text", value="clicked",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_open_shadow_fill_and_submit(make_executor, run_scenario, fixture_url):
    """open shadow 안 input 에 값 + submit → shadow 안 #status 갱신."""
    executor = make_executor()
    page = fixture_url("shadow_open.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        fill("shadow=#form-component >> #name-input", "alice", step=2),
        click("shadow=#form-component >> #submit-btn", step=3),
        verify(
            "shadow=#form-component >> #status", step=4,
            condition="contains_text", value="hello alice",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_closed_shadow_fails_fast(make_executor, run_scenario, fixture_url):
    """closed shadow 안 element 는 자동화 불가 → 시나리오가 즉시 FAIL (hang 없이)."""
    executor = make_executor()
    page = fixture_url("shadow_closed.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click("shadow=#private-form >> #secret-input", step=2),
    ])
    assert results[0].status == "PASS"
    assert results[-1].status == "FAIL"
