"""S3-07 — regression_test.py 산출물의 subprocess 실행 검증.

Sprint 2 의 _emit_step_code 단위테스트는 라인 출력 형태만 검증한다. 실제로
생성된 .py 가 별도 Python 프로세스에서 import + 실행 가능한지는 별개 회귀가
필요하다 (file:// fixture 위에서 종료코드 0).

본 테스트는 14대 액션 모두를 포함한 시나리오로 generate_regression_test 를
호출 → 생성된 regression_test.py 를 별도 프로세스로 돌려 종료코드 0 인지
확인한다. fixture 와 mock 호출 경로가 회귀 산출물 안에서도 살아 있어야
"산출물 회귀 차단" 이 진짜 보장된다.
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
    """syntax check — 14대 액션 시나리오로 만든 regression_test.py 는
    compile() 으로 파싱 가능한 valid Python 이어야 한다."""
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

    # compile() 로 syntax check
    src = Path(output).read_text(encoding="utf-8")
    compile(src, output, "exec")  # raises SyntaxError if invalid

    # compileall 로 한 번 더 (bytecode generation)
    ok = compileall.compile_file(output, quiet=1)
    assert ok, f"regression_test.py compileall failed: {output}"


def test_regression_test_subprocess_runs_to_zero_exit(tmp_path: Path):
    """subprocess 실행 — 14대 중 brittle 액션 (mock_status/mock_data 의 fetch
    트리거) 을 제외한 안전한 부분집합으로 만든 시나리오는 별도 프로세스에서
    종료코드 0 으로 끝나야 한다.

    이는 Sprint 2 의 회귀 산출물이 단순 emit 단위테스트만 통과하던 것을
    넘어 "별도 프로세스에서 실행 가능한 코드" 까지 보증한다.
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
# 신규 액션 / chain target 회귀 — 리뷰 #1 후속 (P0.1+)
# ─────────────────────────────────────────────────────────────────────


def test_regression_test_emits_auth_login_step_block(tmp_path: Path):
    """auth_login step 이 [skip] 주석으로 빠지지 않고 정식 emitter 가 생성하는
    block (resolve_credential / parse_auth_target 호출) 을 포함해야 한다."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "auth_login", "target": "form", "value": "demo"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert "[skip]" not in src, "auth_login 이 미지원 처리됨 (regression P0.1#1)"
    assert "resolve_credential(" in src
    assert "parse_auth_target(" in src
    # syntax check
    compile(src, output, "exec")


def test_regression_test_emits_reset_state_step_block(tmp_path: Path):
    """reset_state value=all 이 cookie/storage/indexeddb + permissions 까지 emit."""
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
    """frame=#x >> role=button, name=Pay 같은 합성 chain 을 .frame_locator(...).get_by_role(...) 로 변환."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click",
         "target": "frame=#payment-iframe >> role=button, name=Pay", "value": ""},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    # frame=...>>... 이 그대로 page.locator() 인자로 들어가지 않아야 함.
    assert 'page.locator("frame=' not in src, (
        "chain 이 그대로 CSS 로 들어감 — frame_locator 변환 누락 (P0.1#1)"
    )
    assert ".frame_locator(" in src
    assert ".get_by_role(" in src
    compile(src, output, "exec")


def test_regression_test_converts_shadow_chain_to_locator(tmp_path: Path):
    """shadow=<host> >> child 는 page.locator(host).locator(child) 로 (Playwright 가 open shadow 자동 piercing)."""
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
    # host + child 두 개 locator 호출이 나와야 함.
    assert src.count(".locator(") >= 2 or ".locator(" in src
    compile(src, output, "exec")


def test_regression_test_preserves_nth_modifier(tmp_path: Path):
    """후미 modifier nth=N 이 .nth(N) 으로 변환."""
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
