"""S3-10 — mock_* 치유 경로 통합 검증.

Sprint 2 의 _execute_mock_step 가 잘못된 URL 패턴을 fallback URL 또는
Dify LLM heal 로 복구하는지 검증. Dify 는 monkeypatch 로 결정론적 응답.

2 케이스:
- fallback URL 패턴 — 1차 패턴이 빈 문자열 등으로 무효 → fallback_targets[0] 으로 복구
- Dify LLM heal — fallback 도 무효 → monkeypatch 된 Dify 응답으로 target 교정
"""

from __future__ import annotations

from helpers.scenarios import click, mock_status, navigate, verify


def test_mock_status_recovers_via_fallback_pattern(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """1차 패턴이 빈 문자열 → fallback_targets 의 정상 패턴으로 복구."""
    recorder = monkeypatch_dify(heal_response=None)
    executor = make_executor()

    scenario = [
        # 1차 target 비어있음 → _install_mock_route 가 ValueError → fallback 으로 진입
        {
            "step": 1,
            "action": "mock_status",
            "target": "",  # 의도적으로 invalid
            "value": "500",
            "fallback_targets": ["**/api/users/*"],
            "description": "패턴 복구 — fallback 으로 정상 글롭 적용",
        },
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        verify("#error", step=4, condition="contains_text", value="(500)"),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert statuses == ["HEALED", "PASS", "PASS", "PASS"], statuses
    assert results[0].heal_stage == "fallback"
    assert scenario_after[0]["target"] == "**/api/users/*"
    assert recorder.heal_calls == 0  # Dify 안 부름


def test_mock_status_recovers_via_dify_llm_heal(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """1차/fallback 모두 invalid → Dify LLM heal 의 결정론적 응답으로 target 교정."""
    recorder = monkeypatch_dify(
        heal_response={"target": "**/api/users/*", "value": "500"},
    )
    executor = make_executor()

    scenario = [
        {
            "step": 1,
            "action": "mock_status",
            "target": "",  # invalid
            "value": "500",
            "fallback_targets": [""],  # 빈 패턴이라 fallback 도 실패
            "description": "Dify LLM heal 까지 도달 — monkeypatch 응답으로 복구",
        },
        navigate(fixture_url("mock_status.html"), step=2),
        click("#load-btn", step=3),
        verify("#error", step=4, condition="contains_text", value="(500)"),
    ]
    results, scenario_after, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert statuses == ["HEALED", "PASS", "PASS", "PASS"], statuses
    assert results[0].heal_stage == "dify"
    assert scenario_after[0]["target"] == "**/api/users/*"
    assert recorder.heal_calls == 1
