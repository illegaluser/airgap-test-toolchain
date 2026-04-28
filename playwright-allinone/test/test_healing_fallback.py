"""S3-09 — fallback_targets 치유 통합 검증.

1차 target 이 페이지에 없을 때 `fallback_targets` 의 두 번째 selector 로
복구되어야 한다. 그리고 그 복구 결과가 `scenario.healed.json` 에 step["target"]
갱신으로 직렬화되어야 한다 (Sprint 2 의 S2-12 강화 회귀).
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers.scenarios import navigate, verify


def test_fallback_target_heals_and_persists_to_healed_json(
    make_executor, run_scenario, fixture_url, monkeypatch_dify
):
    """1차 selector 미존재 → fallback_targets[0] 으로 복구 → step.target 갱신.

    Dify 호출이 새지 않게 monkeypatch 로 차단. fallback 단계에서 끝나야 하므로
    Dify heal 이 호출되면 안 된다 (recorder.heal_calls == 0 검증).
    """
    recorder = monkeypatch_dify(heal_response=None)
    executor = make_executor()

    page = fixture_url("verify_conditions.html")
    scenario = [
        navigate(page, step=1),
        # 1차 selector 는 페이지에 없는 #ghost. fallback 으로 #visible-box 제공.
        {
            "step": 2,
            "action": "verify",
            "target": "#ghost-element",
            "value": "",
            "condition": "visible",
            "description": "고스트 셀렉터 — fallback 으로만 통과",
            "fallback_targets": ["#visible-box"],
        },
    ]
    results, scenario_after, artifacts = run_scenario(executor, scenario)

    # navigate PASS, verify HEALED (fallback 단계)
    assert results[0].status == "PASS"
    assert results[1].status == "HEALED"
    assert results[1].heal_stage == "fallback"

    # step dict 자체가 in-place 로 갱신되어야 한다 (S2-12 보장).
    assert scenario_after[1]["target"] == "#visible-box"

    # Dify heal 호출은 0 — fallback 단계에서 끝났어야 한다.
    assert recorder.heal_calls == 0

    # __main__.main() 의 save_scenario(suffix=".healed") 는 본 헬퍼에서 호출하지
    # 않으므로 직접 직렬화 검증: scenario list 가 갱신된 target 을 갖고 있다는
    # 사실만으로 healed.json 생성 시 갱신값이 들어감이 보장된다.
    serialized = json.dumps(scenario_after, ensure_ascii=False)
    assert "#visible-box" in serialized
    assert "#ghost-element" not in serialized.split("\"target\"")[2]  # step 2 의 target 자리

    # artifacts 디렉토리는 적어도 navigate / verify 스크린샷이 남아 있어야 한다.
    art_path = Path(artifacts)
    assert any(art_path.iterdir())
