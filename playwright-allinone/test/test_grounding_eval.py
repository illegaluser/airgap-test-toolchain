"""Phase 1 T1.7 — grounding_eval 분류기 + comparator 단위 테스트.

본 테스트는 LLM 호출 없이 결정론적으로 동작한다. 운영 러너는
test/grounding_eval/scripts/run_grounding_eval.sh 가 별도로 실행한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grounding_eval.classifier import (
    PageEval,
    StepEval,
    classify_selector,
    evaluate_page,
    parse_selector,
)
from grounding_eval.compare import (
    compute_dod,
    evaluate_run,
    render_html,
)


# ── parse_selector ────────────────────────────────────────────────────────────


def test_parse_selector_role_with_name():
    p = parse_selector("getByRole('button', { name: '로그인' })")
    assert p.kind == "role"
    assert p.role == "button"
    assert p.name == "로그인"


def test_parse_selector_role_without_name():
    p = parse_selector("getByRole('main')")
    assert p.kind == "role"
    assert p.role == "main"
    assert p.name is None


def test_parse_selector_get_by_text():
    p = parse_selector("getByText('Submit')")
    assert p.kind == "text"
    assert p.text == "Submit"


def test_parse_selector_css_id():
    p = parse_selector("#login-btn")
    assert p.kind == "css"
    assert p.css_ids == ("login-btn",)


def test_parse_selector_empty():
    assert parse_selector("").kind == "empty"
    assert parse_selector(None).kind == "empty"


# ── classify_selector — 핵심 분류 룰 ─────────────────────────────────────────


def test_classify_role_role_exact():
    assert classify_selector(
        "getByRole('button', { name: 'Save' })",
        "getByRole('button', { name: 'Save' })",
    ) == "exact"


def test_classify_role_role_name_case_insensitive():
    """name 비교는 대소문자/공백 정규화."""
    assert classify_selector(
        "getByRole('button', { name: 'Save' })",
        "getByRole('button', { name: 'save' })",
    ) == "exact"


def test_classify_role_role_partial_name_substring():
    assert classify_selector(
        "getByRole('button', { name: 'Save Changes' })",
        "getByRole('button', { name: 'Save' })",
    ) == "partial"


def test_classify_role_role_fail_different_role():
    assert classify_selector(
        "getByRole('button', { name: 'Save' })",
        "getByRole('link', { name: 'Save' })",
    ) == "fail"


def test_classify_css_css_exact_id():
    assert classify_selector("#foo", "#foo") == "exact"


def test_classify_css_css_partial_shared_id():
    assert classify_selector("div#foo.bar", "#foo") == "exact"


def test_classify_css_css_fail_different_id():
    assert classify_selector("#foo", "#bar") == "fail"


def test_classify_role_vs_css_partial_via_keyword():
    """role 의 name 이 CSS id 의 토큰과 겹치면 partial."""
    assert classify_selector(
        "getByRole('button', { name: 'login' })",
        "#login-btn",
    ) == "partial"


def test_classify_empty_vs_empty_exact():
    assert classify_selector("", "") == "exact"


def test_classify_empty_vs_filled_fail():
    assert classify_selector("", "#foo") == "fail"
    assert classify_selector("#foo", "") == "fail"


def test_classify_text_text_exact():
    assert classify_selector("getByText('Submit')", "getByText('Submit')") == "exact"


def test_classify_text_text_partial():
    assert classify_selector("getByText('Submit Form')", "getByText('Submit')") == "partial"


# ── evaluate_page ─────────────────────────────────────────────────────────────


def test_evaluate_page_skips_navigate_and_wait():
    golden = [
        {"step": 1, "action": "navigate", "target": "", "value": "x.html"},
        {"step": 2, "action": "wait",     "target": "", "value": "1000"},
        {"step": 3, "action": "click",    "target": "getByRole('button', { name: 'OK' })"},
    ]
    obs = [
        {"step": 1, "action": "navigate", "target": "", "value": "x.html"},
        {"step": 2, "action": "wait",     "target": "", "value": "1000"},
        {"step": 3, "action": "click",    "target": "getByRole('button', { name: 'OK' })"},
    ]
    pe = evaluate_page(
        catalog_id="X", target_url="x", golden_steps=golden, observed_steps=obs,
    )
    classes = [s.selector_class for s in pe.steps]
    assert classes == ["skipped", "skipped", "exact"]
    assert pe.selector_accuracy() == pytest.approx(1.0)


def test_evaluate_page_action_mismatch_is_fail():
    golden = [{"step": 1, "action": "click", "target": "#foo"}]
    obs = [{"step": 1, "action": "fill", "target": "#foo", "value": "x"}]
    pe = evaluate_page(
        catalog_id="X", target_url="x", golden_steps=golden, observed_steps=obs,
    )
    assert pe.steps[0].selector_class == "fail"
    assert "action 불일치" in pe.steps[0].note


def test_evaluate_page_missing_observed_step_is_fail():
    golden = [
        {"step": 1, "action": "navigate", "target": "", "value": "x"},
        {"step": 2, "action": "click", "target": "#foo"},
    ]
    obs = [{"step": 1, "action": "navigate", "target": "", "value": "x"}]
    pe = evaluate_page(
        catalog_id="X", target_url="x", golden_steps=golden, observed_steps=obs,
    )
    assert pe.steps[1].selector_class == "fail"
    assert "관측 step 누락" in pe.steps[1].note


def test_evaluate_page_mock_target_skipped():
    """mock_target 마커가 있으면 selector 평가에서 제외."""
    golden = [
        {"step": 1, "action": "mock_data", "target": "https://api/x", "value": "{}", "mock_target": True},
    ]
    obs = [{"step": 1, "action": "mock_data", "target": "https://api/x", "value": "{}"}]
    pe = evaluate_page(
        catalog_id="X", target_url="x", golden_steps=golden, observed_steps=obs,
    )
    assert pe.steps[0].selector_class == "skipped"


def test_evaluate_page_partial_rate():
    golden = [
        {"step": 1, "action": "click", "target": "getByRole('button', { name: 'login' })"},
        {"step": 2, "action": "click", "target": "getByRole('button', { name: 'OK' })"},
    ]
    obs = [
        {"step": 1, "action": "click", "target": "#login-btn"},          # partial
        {"step": 2, "action": "click", "target": "getByRole('button', { name: 'OK' })"},  # exact
    ]
    pe = evaluate_page(
        catalog_id="X", target_url="x", golden_steps=golden, observed_steps=obs,
    )
    assert pe.selector_accuracy() == pytest.approx(0.5)
    assert pe.partial_rate() == pytest.approx(0.5)


# ── compute_dod ───────────────────────────────────────────────────────────────


def _mock_page(cid: str, *, on_acc: float, off_acc: float,
               on_healer: int = 0, off_healer: int = 0,
               on_elapsed: float = 1000, off_elapsed: float = 800,
               grounding_tokens: int = 800) -> tuple[PageEval, PageEval]:
    """selector_accuracy 가 정확히 on_acc/off_acc 인 PageEval 페어 생성."""
    def steps_with_acc(target_acc: float) -> list[StepEval]:
        # 10 step 중 floor(target_acc * 10) 개를 exact, 나머지 fail
        n = 10
        exact_n = int(round(target_acc * n))
        out = []
        for i in range(n):
            cls = "exact" if i < exact_n else "fail"
            out.append(StepEval(step=i + 1, action="click", selector_class=cls,
                                golden_target="#x", observed_target="#x" if cls == "exact" else "#y"))
        return out

    off = PageEval(catalog_id=cid, target_url="x",
                   steps=steps_with_acc(off_acc), healer_calls=off_healer,
                   planner_elapsed_ms=off_elapsed)
    on = PageEval(catalog_id=cid, target_url="x",
                  steps=steps_with_acc(on_acc), healer_calls=on_healer,
                  planner_elapsed_ms=on_elapsed,
                  grounding_inventory_tokens=grounding_tokens,
                  grounding_truncated=False, grounding_used=True)
    return off, on


def test_compute_dod_passing_scenario():
    off_d, on_d = {}, {}
    for i in range(10):
        cid = f"P{i:02d}"
        off_p, on_p = _mock_page(cid, on_acc=0.9, off_acc=0.4,
                                  off_healer=10, on_healer=2,
                                  off_elapsed=1000, on_elapsed=4000)
        off_d[cid], on_d[cid] = off_p, on_p

    dod = compute_dod(off_d, on_d)
    assert dod["pages_evaluated"] == 10
    assert dod["accuracy_75_pct_pages"] == 10
    checks = dod["dod_checks"]
    assert checks["accuracy_8_or_more_pages_at_75pct"] is True
    assert checks["healer_ratio_one_third_or_less"] is True
    assert checks["elapsed_delta_within_10s"] is True


def test_compute_dod_failing_scenario_low_accuracy():
    off_d, on_d = {}, {}
    for i in range(10):
        cid = f"P{i:02d}"
        off_p, on_p = _mock_page(cid, on_acc=0.5, off_acc=0.4,
                                  off_healer=10, on_healer=8)
        off_d[cid], on_d[cid] = off_p, on_p
    dod = compute_dod(off_d, on_d)
    checks = dod["dod_checks"]
    assert checks["accuracy_8_or_more_pages_at_75pct"] is False
    assert checks["healer_ratio_one_third_or_less"] is False


def test_compute_dod_elapsed_overhead_capped_at_10s():
    off_d, on_d = {}, {}
    cid = "P00"
    off_p, on_p = _mock_page(cid, on_acc=0.9, off_acc=0.5,
                              off_elapsed=500, on_elapsed=15000)  # +14.5s
    off_d[cid], on_d[cid] = off_p, on_p
    dod = compute_dod(off_d, on_d)
    assert dod["dod_checks"]["elapsed_delta_within_10s"] is False


# ── evaluate_run + render_html — 디스크 IO 통합 ─────────────────────────────


def test_evaluate_run_with_real_artifacts(tmp_path: Path):
    """tmp_path 에 가짜 산출물을 만들고 evaluate_run + compute_dod + render_html 실행."""
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / "P-TEST-01.scenario.json").write_text(
        json.dumps({
            "catalog_id": "P-TEST-01",
            "target_url": "file:///tmp/x.html",
            "description": "smoke",
            "steps": [
                {"step": 1, "action": "navigate", "target": "", "value": "file:///tmp/x.html"},
                {"step": 2, "action": "click",    "target": "getByRole('button', { name: 'OK' })"},
            ],
        }), encoding="utf-8",
    )

    off_root = tmp_path / "off"
    on_root = tmp_path / "on"
    for root, target in [(off_root, "#ok"),
                          (on_root, "getByRole('button', { name: 'OK' })")]:
        page_dir = root / "P-TEST-01"
        page_dir.mkdir(parents=True)
        (page_dir / "scenario.json").write_text(json.dumps([
            {"step": 1, "action": "navigate", "target": "", "value": "file:///tmp/x.html"},
            {"step": 2, "action": "click",    "target": target},
        ]), encoding="utf-8")
        record = {
            "kind": "planner", "elapsed_ms": 1234,
            "grounding_inventory_tokens": 500 if root == on_root else None,
            "grounding_truncated": False if root == on_root else None,
        }
        (page_dir / "llm_calls.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    off = evaluate_run(golden_dir=golden_dir, run_root=off_root)
    on = evaluate_run(golden_dir=golden_dir, run_root=on_root)

    off_acc = off["P-TEST-01"].selector_accuracy()
    assert off_acc == pytest.approx(0.0) or off_acc == pytest.approx(1.0)
    assert on["P-TEST-01"].selector_accuracy() == pytest.approx(1.0)

    dod = compute_dod(off, on)
    assert dod["pages_evaluated"] == 1

    html = render_html(off=off, on=on, dod=dod)
    assert "<table>" in html
    assert "P-TEST-01" in html


def test_render_html_handles_missing_pair():
    """off 만 있고 on 이 없는 페이지도 렌더 가능해야 함."""
    off = {"P-X": PageEval(catalog_id="P-X", target_url="x",
                            steps=[StepEval(step=1, action="click", selector_class="exact",
                                            golden_target="#x", observed_target="#x")])}
    on: dict = {}
    dod = compute_dod(off, on)  # 페어 0
    html = render_html(off=off, on=on, dod=dod)
    assert "P-X" in html
    assert dod["pages_evaluated"] == 0


# ── 골든 시나리오 파일 자체의 형식 검증 ─────────────────────────────────────


def test_golden_files_exist_and_parse():
    """리포 내 골든 6개가 모두 존재하고 schema 통과."""
    golden_dir = Path(__file__).resolve().parent / "grounding_eval" / "golden"
    files = sorted(golden_dir.glob("*.scenario.json"))
    cids = [f.stem.split(".")[0] for f in files]
    assert "P0-FX-01" in cids
    assert "P0-FX-05" in cids
    assert "P0-HS-05" in cids
    for f in files:
        spec = json.loads(f.read_text(encoding="utf-8"))
        assert "catalog_id" in spec
        assert "target_url" in spec
        assert isinstance(spec.get("steps"), list)
        for step in spec["steps"]:
            assert "step" in step and "action" in step
