"""Regression guard for c9924c3 — SPA 비동기 마운트 polling-wait fallback.

사용자 실측 (2026-05-15, koreaconnect 포털): step 14 click '확인' (사용신청
완료 팝업) 직후 step 15 click '사용종료' 가 ``role=button, name=사용종료``
resolver 0건 → Dify LLM 치유 → returncode 143 으로 실패.

원인: step 14 의 ``_settle`` 윈도(domcontentloaded 3s + networkidle 2s)가
지났는데도 step 15 의 element 가 아직 DOM 에 마운트 안 됨. executor 의
``_try_initial_target`` 이 resolver None 을 받자마자 곧장 LLM healing chain
(300s timeout) 으로 분기 → 그 동안 실 페이지가 element 를 그려도 회복 못 함.

조치: ``_try_initial_target`` 에 짧은 polling-wait fallback 추가. resolver 가
0건이면 ``RESOLVER_WAIT_TIMEOUT_MS`` (기본 5s) 동안 250ms 주기로 재시도. 늦게
mount 돼도 회복 후 정상 경로 진입. 끝까지 0건이면 기존 healing 으로 떨어짐.

본 슈트는 (a) fixture 가 의도된 1.5s 지연 mount 를 만들어내는지 베이스라인,
(b) executor 가 polling-wait 으로 회복해 step 2 가 PASS/HEALED 되는지 가드.
이 슈트가 사라지면 polling-wait 제거가 silent regression.

Phase 2 폐기 (2026-05-16) 로 사라진 ``test/test_delayed_appear_button_e2e.py``
의 핵심 가드를 e2e-test/ 구조로 복원.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_baseline_step2_button_not_present_initially(page, fixture_url):
    """베이스라인 — 페이지 로드 직후 사용종료 버튼은 DOM 에 없다."""
    page.goto(fixture_url("spa_lazy_mount_button.html"))
    assert page.get_by_role("button", name="사용종료").count() == 0
    assert page.get_by_text("사용종료").count() == 0


@pytest.mark.integration
def test_baseline_step2_button_appears_after_delay(page, fixture_url):
    """베이스라인 — step 1 트리거 후 ~1.5s 뒤에 사용종료 버튼 등장."""
    page.goto(fixture_url("spa_lazy_mount_button.html"))
    page.locator("#btn-step1").click()
    # 1.5s + 여유.
    page.wait_for_selector("#btn-step2", state="attached", timeout=3000)
    assert page.get_by_role("button", name="사용종료").count() == 1


@pytest.mark.integration
def test_executor_polling_wait_recovers_delayed_step_before_llm(
    make_executor, run_scenario, fixture_url,
):
    """회귀 가드 — step 2 가 '사용종료' resolver 0건 → polling-wait → PASS.

    실패 시: ``_try_initial_target`` 이 resolver None 직후 곧장 None 반환,
    caller 가 LLM 치유로 분기하는 회귀.
    """
    page_url = fixture_url("spa_lazy_mount_button.html")
    executor = make_executor()
    results, _, _ = run_scenario(executor, [
        {"step": 1, "action": "navigate", "target": "", "value": page_url,
         "description": "fixture 로드"},
        {"step": 2, "action": "click",
         "target": "role=button, name=트리거", "value": "",
         "description": "step 1 트리거"},
        {"step": 3, "action": "click",
         "target": "role=button, name=사용종료", "value": "",
         "description": "지연 마운트 버튼 클릭 — polling-wait 필요"},
    ])
    statuses = [r.status for r in results]
    assert statuses[0] == "PASS", f"navigate 실패: {results[0]}"
    assert statuses[1] in ("PASS", "HEALED"), f"트리거 실패: {results[1]}"
    assert statuses[2] in ("PASS", "HEALED"), (
        f"지연 마운트 step 실패: {results[2]}\n"
        f"executor 가 resolver 0건 직후 LLM 으로 직행했을 가능성 — "
        f"polling-wait fallback 회귀."
    )
