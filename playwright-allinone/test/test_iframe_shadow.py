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


# ──────────────────────────────────────────────────────────────────────────
# bare iframe[...] chain — codegen 이 frame entry 를 ``frame=`` prefix 없이
# ``page.locator("iframe[...] >> iframe[...] >> #leaf")`` 형태로 emit 한 경우.
# 사용자 portal.koreaconnect.kr SmartEditor 사고 (2026-05-15) 의 회귀 검증.
# resolver 의 _apply_chain_segment 가 bare iframe segment 도 frame_locator
# 로 진입하는지 + 진입 직후 attached wait 으로 race 를 흡수하는지 확인.
# ──────────────────────────────────────────────────────────────────────────


def test_bare_iframe_chain_resolves_without_frame_prefix(
    make_executor, run_scenario, fixture_url,
):
    """``iframe[title="..."] >> iframe[title="..."] >> #leaf`` 형태로도 안쪽
    element 에 도달해 click + verify 가 통과해야 한다.

    이 형태는 codegen 직출 .py 의 ``page.locator("...")`` 한 호출 안에 ``>>``
    합성 selector 가 통째로 들어간 경우에 14-DSL 로 옮겨오면서 만들어진다.
    converter_ast 가 ``frame=`` 로 정규화하기 전 형태이므로 resolver 가
    backward-compat 로 받아내야 한다.
    """
    executor = make_executor()
    page = fixture_url("iframe_codegen_chain.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click(
            'iframe[title="에디터 전체 영역"] >> '
            'iframe[title="편집 모드 영역 -  - CTRL+2:첫 번째 툴바, '
            'CTRL+3:두 번째 툴바"] >> #keditor_body',
            step=2,
        ),
        verify(
            'iframe[title="에디터 전체 영역"] >> '
            'iframe[title="편집 모드 영역 -  - CTRL+2:첫 번째 툴바, '
            'CTRL+3:두 번째 툴바"] >> #editor_status',
            step=3,
            condition="contains_text", value="focused",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"


def test_bare_iframe_chain_with_prefix_title_matcher(
    make_executor, run_scenario, fixture_url,
):
    """동적 title 의 안정 prefix 만 ``[title^="..."]`` 으로 줘도 통과.

    converter_ast 의 _stabilize_iframe_title 이 emit 하는 약화된 selector
    형태 (``iframe[title^="편집 모드 영역"]``) 를 resolver 가 처리할 수
    있는지 확인. SmartEditor 의 단축키 안내 문구가 변동하거나 instance 명이
    바뀌어도 prefix 만 안정적이면 frame 진입 가능한 것이 핵심.
    """
    executor = make_executor()
    page = fixture_url("iframe_codegen_chain.html")
    results, _, _ = run_scenario(executor, [
        navigate(page, step=1),
        click(
            'iframe[title="에디터 전체 영역"] >> '
            'iframe[title^="편집 모드 영역"] >> #keditor_body',
            step=2,
        ),
        verify(
            'iframe[title="에디터 전체 영역"] >> '
            'iframe[title^="편집 모드 영역"] >> #editor_status',
            step=3,
            condition="contains_text", value="focused",
        ),
    ])
    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"실패: {statuses}"
