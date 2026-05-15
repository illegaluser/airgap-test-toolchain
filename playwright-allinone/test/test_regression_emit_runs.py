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


def _make_healed_result(
    step: int, action: str, healed_target: str, heal_stage: str,
    value: str = "",
) -> StepResult:
    """힐링으로 통과한 스텝 — ``target`` 에 *실제로 통과한* selector 를 담는다."""
    return StepResult(
        step_id=step, action=action, target=healed_target, value=value,
        description="", status="HEALED", heal_stage=heal_stage,
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


def test_regression_test_emits_fill_as_press_sequentially(tmp_path: Path):
    """검색창 자동완성 호환 — fill step 4단계 emit (clear + per-keystroke +
    keyup dispatch + 자동완성 settle wait). 2026-05-11 회귀 — Recording UI 의
    ``_do_fill`` 1순위 전략을 완전 미러해야 Replay UI 가 돌리는 회귀 .py 에서
    자동완성 listener 가 트리거되고 dropdown 이 떠서 다음 click step 이 매칭됨.
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "fill", "target": "#search", "value": "hello"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # 1) clear 가 먼저.
    assert '.fill("")' in src, "fill('') 로 입력창 비우는 단계가 없음"
    # 2) press_sequentially.
    assert '.press_sequentially("hello", delay=80)' in src, (
        "press_sequentially 미적용 — 한 글자씩 입력이 안 됨\n" + src
    )
    # 3) keyup dispatch (자동완성 listener 호환).
    assert "KeyboardEvent('keyup'" in src, (
        "keyup 이벤트 dispatch 가 없음 — 자동완성 ajax 트리거 미보장 (FLOW-USR-007 회귀)\n" + src
    )
    # 4) settle wait.
    assert "page.wait_for_timeout(300)" in src, (
        "fill 후 settle wait 가 없음 — 자동완성 응답 대기 안 함\n" + src
    )
    # 옛 한 줄 ``.fill("hello")`` 패턴은 사라져야 함.
    assert '.fill("hello")' not in src, (
        "한 번에 set 하는 fill() 이 그대로 남아 자동완성 회귀 위험"
    )
    compile(src, output, "exec")


def test_regression_test_fill_handles_unicode_value(tmp_path: Path):
    """비-ASCII 값도 press_sequentially 인자로 들어가야 한다.

    2026-05-13 사용자 보고로 regression_generator 가 json.dumps 의 기본을
    ensure_ascii=False 로 바꾼 뒤부터 한글은 escape 없이 원문 그대로 박힌다
    (회귀 .py 가독성). 파일이 UTF-8 로 저장되므로 runtime 동작은 동일.
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "fill", "target": "#search", "value": "요기요"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # 한글이 그대로 박혀야 함 (escape 형태가 아니라 원문).
    assert 'press_sequentially("요기요", delay=80)' in src, (
        f"unicode value 가 press_sequentially 로 안 들어감\n{src}"
    )
    # 옛 escape 형태가 잔존하면 안 됨 — 가독성 회귀 가드.
    assert '\\uc694\\uae30\\uc694' not in src, (
        f"한글이 \\uXXXX escape 로 떨어졌다 — 가독성 회귀\n{src}"
    )
    compile(src, output, "exec")


def test_regression_test_select_waits_before_select_option(tmp_path: Path):
    """select step 은 select_option 직전 wait_for(attached) 로 element 자리잡음 대기.

    2026-05-11 FLOW-USR-007 step 14 회귀 — ``combobox.nth(1).select_option(label="50")``
    가 element 가 ajax 로 늦게 attach 되는 사이트에서 30s timeout 으로 깨졌음.
    Recording UI 의 LLM Play 는 executor 의 자체 retry 로 통과하지만, raw 회귀본은
    healing 없어서 정확히 같은 명령 한 번에 fail. wait_for(attached, 15000) 로
    명시적 대기 후 select_option 호출.
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "select", "target": "role=combobox, nth=1", "value": "50"},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # wait_for(attached) 가 select_option 보다 *먼저* 와야.
    wait_idx = src.find("wait_for(state='attached'")
    select_idx = src.find("select_option")
    assert wait_idx >= 0, f"wait_for(attached) emit 누락\n{src}"
    assert select_idx > wait_idx, (
        f"wait_for 가 select_option 보다 *뒤* 에 옴 — 순서 보장 실패\n{src}"
    )
    # 옛 한 줄 emit 사라져야.
    assert ".combobox).first.select_option(" not in src
    compile(src, output, "exec")


def test_regression_test_splits_exact_modifier_from_name(tmp_path: Path):
    """``name=텍스트, exact=true`` 후미 modifier 는 ``exact=True`` kwarg 로 분리.

    2026-05-11 FLOW-USR-007 사례 — ``role=button, name=검색, exact=true`` 가
    회귀 .py 에 ``name="검색, exact=true"`` 통째로 박혀 Playwright 가 실 버튼을
    못 찾고 5초 timeout 회귀. executor 의 resolver 와 동일 의미로 분리해야 함.
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click",
         "target": "role=button, name=검색, exact=true", "value": ""},
        {"step": 3, "action": "click",
         "target": "role=button, name=Login, exact=false", "value": ""},
        {"step": 4, "action": "click",
         "target": "role=button, name=Without Modifier", "value": ""},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # exact=true 케이스 — name 은 순수, exact=True kwarg 분리.
    assert 'name="\\uac80\\uc0c9", exact=True' in src, (
        "exact=true 가 분리되지 않고 name 안에 박혀 있음\n" + src
    )
    assert '"\\uac80\\uc0c9, exact=true"' not in src, (
        "exact 가 name 안에 새어 들어감"
    )
    # exact=false 케이스 — exact=True 안 박혀야 하고, name 도 순수.
    assert '"Login, exact=false"' not in src, "exact=false 가 name 안에 박힘"
    # modifier 없는 케이스 — 기존 형태 유지.
    assert '"Without Modifier"' in src
    assert 'name="Without Modifier", exact' not in src, (
        "modifier 없는 케이스에 exact kwarg 가 임의로 들어감"
    )
    compile(src, output, "exec")


def test_regression_test_preserves_nth_modifier(tmp_path: Path):
    """후미 modifier nth=N 이 .nth(N) 으로 변환 + ``.first`` 선행 금지.

    ``.first.nth(N)`` 은 Playwright 의미상 N≥1 에서 항상 빈 매치라 5s timeout 회귀
    (2026-05-11 FLOW-USR-006 사례 — LLM 회귀본이 ``page.locator("button").first.nth(5)`` 로
    emit 되어 Replay UI 에서 매번 timeout).
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click",
         "target": "role=link, name=Read more, nth=2", "value": ""},
        {"step": 3, "action": "click", "target": "button, nth=5", "value": ""},
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert ".nth(2)" in src
    assert ".nth(5)" in src
    assert ".first.nth(" not in src, (
        ".first.nth(N) 은 Playwright 의미상 빈 매치 — modifier 경로에서 .first 부착 금지"
    )
    compile(src, output, "exec")


# ─────────────────────────────────────────────────────────────────────
# Healing-aware emit — 2026-05-11 수정 회귀
# ─────────────────────────────────────────────────────────────────────


def test_regression_test_emits_healed_locator_from_results(tmp_path: Path):
    """힐링으로 통과한 스텝은 ``results[i].target`` 의 healed selector 가
    회귀 .py 에 들어가야 한다 (원본 fragile selector 가 새지 않는다).

    설계 의도: regression_test.py 는 셀프힐링을 거쳐 *최종 통과한* locator 의
    스냅샷이다. 이전 구현은 ``scenario[i].target`` 만 봐서 원본만 emit 했음.
    """
    # 영문 healed target — ASCII 만 사용해 emit 포맷을 인코딩 의존성 없이 검증.
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click", "target": "#userinintro", "value": ""},
    ]
    results = [
        _make_pass_result(1, "navigate", target="", value="https://example.test"),
        _make_healed_result(2, "click", healed_target="text=Switch to English",
                            heal_stage="local"),
    ]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # 원본 fragile selector (#userinintro) 는 본문 코드에 등장하면 안 된다 —
    # 주석 (original target: ...) 에는 등장 가능.
    assert "page.locator(\"#userinintro\")" not in src, (
        "원본 selector 가 회귀 본문에 그대로 들어감 — healing-aware emit 미적용"
    )
    # healed selector 가 실제 Playwright 호출로 컴파일되어야 한다.
    assert 'page.get_by_text("Switch to English")' in src, (
        "healed text= selector 가 get_by_text 호출로 변환되지 않음"
    )
    # 주석에는 원본 target 가 노출되어 fragile 지점을 식별할 수 있어야 한다.
    assert "'#userinintro'" in src
    assert "[HEALED via local]" in src
    compile(src, output, "exec")


def test_regression_test_emits_heal_stage_comment(tmp_path: Path):
    """힐링 케이스에서 ``# [HEALED via <stage>] original target: ...`` 주석이
    스텝 직전에 emit 되어야 한다 — 운영자가 fragile 지점을 식별할 단서."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click", "target": "#old-btn", "value": ""},
        {"step": 3, "action": "click", "target": "#kept-btn", "value": ""},
    ]
    results = [
        _make_pass_result(1, "navigate", target="", value="https://example.test"),
        _make_healed_result(2, "click", healed_target="text=확인",
                            heal_stage="dify"),
        _make_pass_result(3, "click", target="#kept-btn"),
    ]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # 힐링된 스텝에는 주석이 있어야 한다.
    assert "[HEALED via dify]" in src
    assert "'#old-btn'" in src, "original target 가 주석에 노출되어야 함"
    # 힐링 안 거친 스텝 (#kept-btn) 의 *바로 앞* 비어있지 않은 줄이 HEALED
    # 주석이면 안 된다 — 잘못 붙은 표식 차단.
    lines = src.splitlines()
    kept_line_idx = next(
        i for i, ln in enumerate(lines) if "#kept-btn" in ln and "[HEALED" not in ln
    )
    # 바로 앞 비어있지 않은 줄 추적.
    j = kept_line_idx - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    assert j < 0 or "[HEALED" not in lines[j], (
        f"PASS 스텝에 잘못 붙은 HEALED 주석 — 직전 라인: {lines[j]!r}"
    )
    compile(src, output, "exec")


def test_regression_test_emits_visibility_heal_pre_actions(tmp_path: Path):
    """visibility healer 가 cascade hover 로 통과시킨 케이스 — ``pre_actions``
    가 본 스텝 click *앞에* hover + wait_for_timeout 시퀀스로 emit 되어야 한다.

    설계 의도 (2026-05-11): Replay UI 의 raw wrapper 는 healing 안전망이 없다.
    Recording UI executor 가 *통과시킨 시퀀스* 자체를 회귀 .py 에 박아 같은
    환경에서 동등하게 통과시킨다 (안전망을 매번 다시 돌리지 않음).
    """
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click", "target": "#userinintro", "value": ""},
    ]
    results = [
        _make_pass_result(1, "navigate", target="", value="https://example.test"),
        StepResult(
            step_id=2, action="click", target="#userinintro", value="",
            description="", status="PASS", heal_stage="none",
            pre_actions=[
                {"action": "hover", "target": "#gnbBox > li:nth-of-type(3)"},
                {"action": "hover", "target": "#gnbBox > li:nth-of-type(3) > a"},
            ],
        ),
    ]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")

    # 두 단계 hover 가 본 스텝 click 앞에 순서대로 emit 되어야 한다.
    assert '#gnbBox > li:nth-of-type(3)' in src
    assert '.hover(timeout=1500)' in src
    assert 'wait_for_timeout(150)' in src
    # 순서 검증 — hover 가 click 보다 앞에 있어야 한다.
    hover_idx = src.index('#gnbBox > li:nth-of-type(3)')
    click_idx = src.index('"#userinintro"')
    assert hover_idx < click_idx, "hover 시퀀스가 본 스텝 click 뒤로 밀림"
    # PRE 마커 주석으로 운영자가 식별 가능.
    assert "[PRE] visibility heal — hover ancestor" in src
    compile(src, output, "exec")


def test_regression_test_no_pre_actions_when_step_passed_clean(tmp_path: Path):
    """일반 PASS (visibility heal 작동 안 함) 케이스에는 pre_actions 가 없으니
    회귀 .py 에도 [PRE] hover 가 들어가지 않아야 한다."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click", "target": "#btn-clean", "value": ""},
    ]
    results = [
        _make_pass_result(1, "navigate", target="", value="https://example.test"),
        _make_pass_result(2, "click", target="#btn-clean"),
    ]

    output = generate_regression_test(scenario, results, str(tmp_path))
    src = Path(output).read_text(encoding="utf-8")
    assert "[PRE]" not in src
    compile(src, output, "exec")


def test_regression_test_falls_back_to_scenario_target_when_results_missing(
    tmp_path: Path,
):
    """방어선 — results 가 scenario 보다 짧거나 r.target 이 비어 있으면
    scenario 의 원본 target 으로 fallback (regression 안전망)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {"step": 2, "action": "click", "target": "#fallback-btn", "value": ""},
    ]
    # results 가 일부러 짧음 — 인덱스 1 (두 번째 스텝) 결과 없음.
    results = [_make_pass_result(1, "navigate", target="", value="https://example.test")]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert "#fallback-btn" in src
    compile(src, output, "exec")


def test_regression_test_bare_iframe_chain_uses_frame_locator(tmp_path: Path):
    """Playwright codegen 의 ``locator("iframe[...] >> #child")`` 형태 (frame=
    prefix 없음) 가 14-DSL target 으로 그대로 옮겨졌을 때, regression_generator
    가 bare ``iframe[...]`` segment 를 자동으로 ``.frame_locator(...)`` 로
    변환해야 한다. 변환 누락 시 회귀 .py 는 메인 DOM 에서만 child 를 찾으려
    들어 15s timeout 으로 깨진다 (2026-05-15 portal.koreaconnect SmartEditor
    `#keditor_body` 회귀 보고)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {
            "step": 2, "action": "click",
            "target": 'iframe[title="에디터"] >> #keditor_body',
            "value": "",
        },
    ]
    results = [_make_pass_result(s["step"], s["action"]) for s in scenario]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    assert ".frame_locator(" in src, (
        "bare iframe[...] segment 가 frame_locator 로 변환되지 않음"
    )
    # iframe[...] selector 가 raw 로 page.locator() 인자로 그대로 들어가면 안 됨.
    assert 'page.locator("iframe[' not in src
    compile(src, output, "exec")


def test_regression_test_preserves_frame_chain_when_stable_selector_present(
    tmp_path: Path,
):
    """이중 iframe 안 element 에서 executor 가 stable_selector (leaf id) 만
    캡처했을 때, regression_generator 는 원본 scenario target 의 frame chain
    prefix 를 stable_selector 앞에 붙여 frame_locator 진입을 보존해야 한다.
    보존 누락 시 회귀 .py 는 메인 DOM 의 `#keditor_body` 를 찾으려 들어 15s
    timeout 으로 깨진다 (실제 회귀: 2026-05-15 portal.koreaconnect 보고)."""
    scenario = [
        {"step": 1, "action": "navigate", "target": "", "value": "https://example.test"},
        {
            "step": 2, "action": "click",
            "target": (
                'iframe[title="에디터 전체 영역"] >> '
                'iframe[title="편집 모드 영역"] >> #keditor_body'
            ),
            "value": "",
        },
    ]
    # executor 가 leaf id 만 stable_selector 로 캡처한 상황을 재현.
    r2 = StepResult(
        step_id=2, action="click", target="", value="",
        description="", status="PASS", heal_stage="none",
        stable_selector="#keditor_body",
    )
    results = [_make_pass_result(1, "navigate", value="https://example.test"), r2]

    output = generate_regression_test(scenario, results, str(tmp_path))
    assert output is not None
    src = Path(output).read_text(encoding="utf-8")
    # frame_locator 호출이 최소 2회 (이중 iframe) 등장해야 함.
    assert src.count(".frame_locator(") >= 2, (
        "frame chain prefix 가 stable_selector 앞에 보존되지 않음 — "
        "회귀 .py 가 메인 DOM 에서 #keditor_body 를 찾게 됨"
    )
    # leaf id 자체는 emit 되어야 함.
    assert "#keditor_body" in src
    compile(src, output, "exec")
