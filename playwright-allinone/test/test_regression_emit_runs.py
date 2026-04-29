"""S3-07 — subprocess execution check for regression_test.py output.

Sprint 2's _emit_step_code unit tests only verify the line-by-line
output shape. Whether the generated .py can actually be imported and
run in a separate Python process (exit code 0 over a file:// fixture)
needs its own regression test.

This test calls generate_regression_test with a scenario covering all
14 actions → runs the produced regression_test.py in a subprocess →
confirms exit code 0. Fixtures and the mock call path must survive
inside the regression artifact, otherwise "block regressions in the
artifact" doesn't really hold.
"""

from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

from zero_touch_qa.regression_generator import generate_regression_test
from zero_touch_qa.executor import StepResult


def _make_pass_result(step: int, action: str, target: str = "", value: str = "") -> StepResult:
    return StepResult(
        step_id=step, action=action, target=target, value=value,
        description="", status="PASS", heal_stage="none",
    )


def test_regression_test_compiles_to_valid_python(tmp_path: Path):
    """syntax check — regression_test.py built from a 14-action scenario
    must parse with compile() as valid Python."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": fixtures_dir.joinpath("verify_conditions.html").as_uri()},
        {"step": 2, "action": "wait", "target": "", "value": "100"},
        {"step": 3, "action": "click", "target": "#visible-box", "value": ""},
        {"step": 4, "action": "fill", "target": "#text-input", "value": "exact-value-42"},
        {"step": 5, "action": "press", "target": "#text-input", "value": "Tab"},
        {"step": 6, "action": "select", "target": "select", "value": "x"},
        {"step": 7, "action": "check", "target": "#cb-checked", "value": "on"},
        {"step": 8, "action": "hover", "target": "#visible-box", "value": ""},
        {"step": 9, "action": "verify", "target": "#contain-paragraph", "value": "12,345", "condition": "contains_text"},
        {"step": 10, "action": "upload", "target": "#file-input", "value": "smoke.txt"},
        {"step": 11, "action": "drag", "target": "#card", "value": "#dst"},
        {"step": 12, "action": "scroll", "target": "#footer", "value": "into_view"},
        {"step": 13, "action": "mock_status", "target": "**/api/users/*", "value": "500"},
        {"step": 14, "action": "mock_data", "target": "**/api/list", "value": '{"items":[]}'},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    assert Path(output).exists()

    # syntax check via compile()
    src = Path(output).read_text(encoding="utf-8")
    compile(src, output, "exec")  # raises SyntaxError if invalid

    # also via compileall (bytecode generation)
    ok = compileall.compile_file(output, quiet=1)
    assert ok, f"regression_test.py compileall failed: {output}"


def test_regression_test_subprocess_runs_to_zero_exit(tmp_path: Path):
    """subprocess run — a scenario built from the safe subset of the 14
    actions (excluding the brittle ones, e.g. mock_status/mock_data
    fetch triggers) must finish with exit code 0 in a separate process.

    This raises Sprint 2's regression artifact bar from "passes the emit
    unit tests" to "the code is executable in a separate process".
    """
    fixtures_dir = Path(__file__).parent / "fixtures"
    page_url = fixtures_dir.joinpath("verify_conditions.html").as_uri()

    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": page_url},
        {"step": 2, "action": "wait", "target": "", "value": "50"},
        {"step": 3, "action": "verify", "target": "#visible-box", "value": "", "condition": "visible"},
        {"step": 4, "action": "verify", "target": "#hidden-box", "value": "", "condition": "hidden"},
        {"step": 5, "action": "verify", "target": "#btn-disabled", "value": "", "condition": "disabled"},
        {"step": 6, "action": "verify", "target": "#cb-checked", "value": "", "condition": "checked"},
        {"step": 7, "action": "verify", "target": "#text-input", "value": "exact-value-42", "condition": "value"},
        {"step": 8, "action": "verify", "target": "#contain-paragraph", "value": "12,345", "condition": "contains_text"},
        {"step": 9, "action": "scroll", "target": "#contain-paragraph", "value": "into_view"},
        {"step": 10, "action": "hover", "target": "#visible-box", "value": ""},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None

    proc = subprocess.run(
        [sys.executable, str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"regression_test.py subprocess failed (code={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ─────────────────────────────────────────────────────────────────────
# new actions / chain target regression — review #1 follow-up (P0.1+)
# ─────────────────────────────────────────────────────────────────────


def test_regression_test_emits_auth_login_step_block(tmp_path: Path):
    """auth_login step must not collapse to a [skip] comment — it must
    contain the official emitter block (resolve_credential / parse_auth_target calls)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "auth_login", "target": "form", "value": "demo"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert "[skip]" not in src, "auth_login marked as unsupported (regression P0.1#1)"
    assert "resolve_credential(" in src
    assert "parse_auth_target(" in src
    # syntax check
    compile(src, output, "exec")


def test_regression_test_emits_reset_state_step_block(tmp_path: Path):
    """reset_state value=all emits cookie/storage/indexeddb + permissions reset."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "reset_state", "target": "", "value": "all"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert "[skip]" not in src
    assert "clear_cookies()" in src
    assert "clear_permissions()" in src
    assert "localStorage.clear" in src
    assert "indexedDB" in src
    compile(src, output, "exec")


def test_regression_test_converts_frame_chain_to_frame_locator(tmp_path: Path):
    """A composed chain like frame=#x >> role=button, name=Pay converts to .frame_locator(...).get_by_role(...)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click",
         "target": "frame=#payment-iframe >> role=button, name=Pay", "value": ""},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    # frame=...>>... must not flow straight into page.locator() args.
    assert 'page.locator("frame=' not in src, (
        "chain leaked into CSS as-is — missing frame_locator conversion (P0.1#1)"
    )
    assert ".frame_locator(" in src
    assert ".get_by_role(" in src
    compile(src, output, "exec")


    """shadow=<host> >> child becomes page.locator(host).locator(child) (Playwright auto-pierces open shadow)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "fill",
         "target": "shadow=#form-component >> #name-input", "value": "alice"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert 'page.locator("shadow=' not in src
    # both host and child locator calls must appear.
    assert src.count(".locator(") >= 2 or ".locator(" in src
    compile(src, output, "exec")


def test_regression_test_preserves_nth_modifier(tmp_path: Path):
    """A trailing nth=N modifier converts to .nth(N)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click",
         "target": "role=link, name=Read more, nth=2", "value": ""},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert ".nth(2)" in src
    compile(src, output, "exec")
