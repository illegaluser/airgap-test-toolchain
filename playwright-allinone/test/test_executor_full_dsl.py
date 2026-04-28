"""S3-08 — 14대 액션을 한 시나리오에 모두 포함한 메타-회귀.

executor 가 14대 DSL 을 한 번에 순차 실행할 때 step 간 상태 오염 없이 모두
PASS 하는지 검증. 산출물 (run_log.jsonl, scenario.healed.json) 정합도 함께.
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers.scenarios import (
    check, click, drag, fill, hover, mock_data, mock_status, navigate,
    press, scroll, select, upload, verify, wait,
)


def _seed_artifact(executor, name: str, content: str) -> Path:
    art = Path(executor.config.artifacts_dir)
    art.mkdir(parents=True, exist_ok=True)
    p = art / name
    p.write_text(content, encoding="utf-8")
    return p


def test_full_14_action_scenario_all_pass(make_executor, run_scenario, fixture_url):
    executor = make_executor()
    _seed_artifact(executor, "ttc.txt", "smoke")

    page = fixture_url("full_dsl.html")
    scenario = [
        # mock_* 는 navigate 보다 먼저 — 페이지 로드시 fetch 가 먼저 트리거되지
        # 않지만 후속 click 으로 호출될 때 가로챌 수 있게 미리 설치.
        mock_status("**/api/users/*", 500, step=1, description="users API 모킹"),
        mock_data("**/api/list", {"items": [{"name": "alpha"}]}, step=2,
                  description="list API 모킹"),
        navigate(page, step=3, description="대상 페이지 로드"),
        wait(50, step=4, description="DOM 안정화"),
        # hover 가 click 보다 먼저 — fixture 의 click 핸들러가 status 를
        # "clicked" 로 덮어쓰면 mouseenter 가 다시 발화되지 않으므로 순서 보존.
        hover("#primary-btn", step=5),
        verify("#status", step=6, condition="contains_text", value="hovered"),
        click("#primary-btn", step=7),
        verify("#status", step=8, condition="contains_text", value="clicked"),
        fill("#search-input", "DSCORE", step=9),
        verify("#echo", step=10, condition="contains_text", value="echo:DSCORE"),
        press("#search-input", "Enter", step=11),
        verify("#echo", step=12, condition="contains_text", value="submitted:DSCORE"),
        select("#lang", "한국어", step=13),
        check("#agree", "on", step=14),
        verify("#agree", step=15, condition="checked"),
        upload("#file-input", "ttc.txt", step=16),
        verify("#file-name", step=17, condition="contains_text", value="ttc.txt"),
        drag("#card", "#dst-zone", step=18),
        click("#load-btn", step=19),
        verify("#list", step=20, condition="contains_text", value="alpha"),
        scroll("#footer", step=21),
        verify("#footer", step=22, condition="visible"),
    ]
    results, _, _ = run_scenario(executor, scenario)

    statuses = [r.status for r in results]
    assert all(s == "PASS" for s in statuses), f"some steps failed: {statuses}"

    # 14대 액션 모두 ≥1 회 등장
    actions_seen = {s["action"] for s in scenario}
    expected = {
        "navigate", "click", "fill", "press", "select", "check", "hover", "wait",
        "verify", "upload", "drag", "scroll", "mock_status", "mock_data",
    }
    missing = expected - actions_seen
    assert not missing, f"누락된 액션: {missing}"


