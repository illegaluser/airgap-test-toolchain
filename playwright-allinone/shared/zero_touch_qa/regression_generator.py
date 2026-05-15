import json as _json
import os
import re
import time
import logging

from .executor import StepResult
# name=<텍스트>, exact=true 같은 후미 옵션을 분리해 ``exact=`` kwarg 로 emit 하기
# 위해 executor 의 resolver 와 동일 헬퍼 재사용. 자체 정의하면 두 곳에서 정규식이
# 어긋날 위험.
from .locator_resolver import _IFRAME_SELECTOR_RE, _split_name_exact
from .converter_ast import _split_iframe_chain


class _DumpsShim:
    """json.dumps wrapper — ensure_ascii=False 가 *기본* 이 되도록 강제.

    회귀 .py 의 selector / value / URL / DOM 패턴 등 모든 emit 자리에서
    한글이 ``\\uXXXX`` escape 로 떨어지던 가독성 회귀 (2026-05-13 사용자 보고)
    방지. 호출 측이 명시적으로 ensure_ascii=True 를 넘기지 않는 한 그대로
    한글 문자 유지.
    """
    @staticmethod
    def dumps(obj, **kw):
        kw.setdefault("ensure_ascii", False)
        return _json.dumps(obj, **kw)

    # 옛 ``import json; json.loads(...)`` 등 dumps 외 attribute 호환.
    def __getattr__(self, name):
        return getattr(_json, name)


json = _DumpsShim()

log = logging.getLogger(__name__)

# 회귀 스크립트가 emit 하는 try/except 한 줄 — 동일 라인이 여러 emitter 에서
# 반복돼 lint(S1192) 가 잡히므로 상수로 박는다. 들여쓰기는 emit 자리와 동일.
_EXCEPT_PASS_LINE = "            except Exception: pass"


def _frame_chain_prefix(target) -> str:
    """``iframe[...] >> ... >> leaf`` 형태에서 frame entry 부분만 추출.

    stable_selector 가 leaf id 만 갖고 있을 때 회귀 .py 에 frame chain 진입을
    보존하기 위해 호출. target 이 chain 이 아니거나 frame 진입이 없으면 빈
    문자열 (caller 가 stable_selector 단독으로 사용).
    """
    if not isinstance(target, str) or " >> " not in target:
        return ""
    frames, _leaf = _split_iframe_chain(target)
    return " >> ".join(frames)


def _build_header_lines(needs_auth_imports: bool) -> list[str]:
    """회귀 .py 의 prologue — module docstring / import / context / 헬퍼 정의.

    분리 이유: generate_regression_test 의 cognitive complexity 가 step loop
    뿐 아니라 거대한 header 리터럴 까지 합쳐 폭발하던 구조 정리. emit 내용
    자체는 변경 없음 (헬퍼 추출만).
    """
    lines = [
        '"""',
        "Auto-generated regression test from Zero-Touch QA scenario.",
        "LLM 없이 독립 실행 가능한 Playwright 스크립트.",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        '"""',
        "from playwright.sync_api import sync_playwright",
    ]
    if needs_auth_imports:
        # auth_login 은 zero_touch_qa.auth 의 credential lookup + TOTP 생성을
        # 재사용해야 자체 완결. 회귀 스크립트가 zero_touch_qa 패키지와 같은
        # PYTHONPATH 에서 실행되는 것을 전제. (artifacts/ 위치 기본 가정.)
        lines.append(
            "from zero_touch_qa.auth import (\n"
            "    EMAIL_FIELD_CANDIDATES, PASSWORD_FIELD_CANDIDATES,\n"
            "    SUBMIT_BUTTON_CANDIDATES, TOTP_FIELD_CANDIDATES,\n"
            "    generate_totp_code, parse_auth_target, resolve_credential,\n"
            ")"
        )
    # 운영 원칙: 화면 띄움(headed) 이 기본. 헤드리스는 사용자가 명시적으로
    # 옵트인 (env var ``REGRESSION_HEADLESS=1`` 또는 pre-commit 슈트에서 강제).
    # 설계 근거: orchestrator.py:128 (D9 — 운영 기본은 headed) +
    # replay_proxy.py:325-329 (wrapper monkey-patch 와 동일 의도).
    #
    # native dialog (alert / confirm / beforeunload / prompt) 자동 dismiss —
    # executor.py:412-446 와 동기화. 사이트가 외부 이동 confirm 등 native dialog
    # 띄우면 핸들러 없을 시 raw .py 가 dialog 위에 가려져 후속 selector 가 못
    # 잡히거나 click 자체가 멈추는 회귀 (2026-05-13 사용자 보고 — aebc6756b737).
    lines.extend([
        "",
        "",
        "import os",
        "",
        "",
        "def test_regression():",
        '    _headless = os.environ.get("REGRESSION_HEADLESS", "").lower() in ("1", "true", "yes")',
        '    with sync_playwright() as p:',
        "        browser = p.chromium.launch(headless=_headless)",
        "        context = browser.new_context(",
        '            viewport={"width": 1440, "height": 900},',
        '            locale="ko-KR",',
        "        )",
        "        # 모든 page(메인 + popup) 에 dialog auto-dismiss 핸들러 등록.",
        "        # executor 와 동일 의미 — Playwright 기본 동작이 'no-op + 멈춤' 이라",
        "        # 명시적 dismiss 가 없으면 confirm/alert 가 흐름을 막는다.",
        "        def _auto_dismiss_dialog(d):",
        "            try: d.dismiss()",
        _EXCEPT_PASS_LINE,
        "        context.on('page', lambda _p: _p.on('dialog', _auto_dismiss_dialog))",
        "        page = context.new_page()",
        "        page.on('dialog', _auto_dismiss_dialog)",
        "",
        "        # 각 action 직후 사이트 반응이 완료될 때까지 동적 대기. 단순 고정",
        "        # sleep 보다 빠르고 안정. SPA 의 long-poll 처럼 영영 idle 안 오는",
        "        # 케이스는 timeout 후 silent 진행 (try/except 흡수).",
        "        # 옵트인 off: REGRESSION_STEP_WAIT_TIMEOUT_MS=0 (예: pre-commit 슈트).",
        "        _step_wait_ms = int(os.environ.get('REGRESSION_STEP_WAIT_TIMEOUT_MS', '3000'))",
        "        # 각 action (click/select/drag/scroll/...) 의 Playwright timeout.",
        "        # env REGRESSION_ACTION_TIMEOUT_MS — 기본 15000ms. 느린 사이트나",
        "        # 비동기 trigger 가 길게 걸리는 케이스는 늘려 시도 가능.",
        "        _action_timeout_ms = int(os.environ.get('REGRESSION_ACTION_TIMEOUT_MS', '15000'))",
        "        # SPA long-poll 사이트는 networkidle 영영 안 도달해 silent skip 됨.",
        "        # 메모리 룰: executor 와 동일한 step 간 대기 (1.5s) 적용 — networkidle",
        "        # 미도달 시 fallback stabilization sleep 으로 자동완성 panel / 모달",
        "        # dismiss 같은 비동기 반응이 완료될 시간을 보장 (2026-05-14 g2).",
        "        _settle_fallback_ms = int(os.environ.get('REGRESSION_SETTLE_FALLBACK_MS', '1500'))",
        "        def _settle(p):",
        "            if _step_wait_ms <= 0:",
        "                return",
        "            try:",
        "                p.wait_for_load_state('networkidle', timeout=_step_wait_ms)",
        "            except Exception:",
        "                # networkidle 미도달 — SPA / long-poll. 짧은 fallback sleep 보강.",
        "                if _settle_fallback_ms > 0:",
        "                    try:",
        "                        p.wait_for_timeout(_settle_fallback_ms)",
        "                    except Exception:",
        "                        pass",
        "        # 클릭이 actionability 검사로 거부된 경우 (요소가 닫히는 레이어에",
        "        # 가려졌거나 height:0 / line-height:0 같은 computed style 으로 인해",
        "        # Playwright 의 hit-test 가 실패) JS dispatch click 으로 폴백.",
        "        # anchor/button/clickable role 일 때만 — 그 외 요소엔 실 사이트 listener",
        "        # 가 없어 false-positive PASS 가 될 수 있어 그대로 raise. executor 의",
        "        # 동일 패턴(executor.py:3183-3205) 미러.",
        "        def _safe_click(loc, *, timeout):",
        "            try:",
        "                loc.click(timeout=timeout)",
        "                return",
        "            except Exception as click_err:",
        "                msg = str(click_err)",
        "                if not any(s in msg for s in (",
        "                    'not visible', 'outside of the viewport',",
        "                    'intercepts pointer events', 'Element is not stable',",
        "                    'Timeout',",
        "                )):",
        "                    raise",
        "                try:",
        "                    info = loc.evaluate(",
        '                        "el => ({tag: (el.tagName||\\"\\").toLowerCase(),"',
        '                        " role: ((el.getAttribute&&el.getAttribute(\\"role\\"))||\\"\\").toLowerCase(),"',
        '                        " onclick: typeof el.onclick === \\"function\\"})"',
        "                    )",
        "                except Exception:",
        "                    raise click_err",
        "                tag = info.get('tag') if isinstance(info, dict) else ''",
        "                role = info.get('role') if isinstance(info, dict) else ''",
        "                onclick = info.get('onclick') if isinstance(info, dict) else False",
        "                safe = (tag in ('a','button')) or (role in ('button','link','menuitem','tab','option','checkbox')) or onclick",
        "                if not safe:",
        "                    raise",
        "                loc.evaluate('el => el.click()')",
        "        try:",
    ])
    return lines


def _build_footer_lines() -> list[str]:
    """회귀 .py 의 epilogue — finally + main entry. header 와 짝."""
    return [
        "        finally:",
        "            context.close()",
        "            browser.close()",
        "",
        "",
        'if __name__ == "__main__":',
        "    test_regression()",
        '    print("Regression test passed.")',
        "",
    ]


def _resolve_target_from_result(r, scen_target):
    """StepResult 와 원본 scenario target 에서 회귀 .py 가 들고 갈 target 산출.

    stable_selector 가 있으면 leaf id 우선이지만, 원본 target 이 iframe chain
    이면 그 frame prefix 를 보존해 frame context 손실 차단. stable 이 없으면
    healed target → scen_target 순으로 fallback.
    """
    stable = getattr(r, "stable_selector", "") or ""
    if not stable:
        return r.target if r.target else scen_target
    frame_prefix = _frame_chain_prefix(scen_target) or _frame_chain_prefix(r.target)
    return (frame_prefix + " >> " + stable) if frame_prefix else stable


def _resolve_step_io(step: dict, r):
    """healing override 로 (action, target, value, heal_stage) 정리.

    fallback / alternative / local / dify 단계가 통과시킨 *실제 selector* 가
    있으면 그 쪽을 들고 나간다. target 결정 로직은 ``_resolve_target_from_result``
    로 위임 — frame chain 보존 vs leaf 우선 분기를 한 곳에서 관리.
    """
    scen_action = step["action"].lower()
    scen_target = step.get("target", "")
    scen_value = step.get("value", "")
    if r is None:
        return scen_action, scen_target, scen_value, "none"

    action = ((r.action or scen_action) or "").lower()
    target = _resolve_target_from_result(r, scen_target)
    # scenario value 가 dict/list (mock_data) 면 보존 — StepResult.value 는
    # 항상 str 이라 직렬화 손실이 발생함.
    if isinstance(scen_value, (dict, list)):
        value = scen_value
    else:
        value = r.value if r.value else scen_value
    return action, target, value, (r.heal_stage or "none")


def _emit_popup_focus_lines(page_var: str, last_popup_created) -> list[str]:
    """직전 step 이 popup 을 만들었고 이번 step 이 다른 page 면 focus 회복."""
    if not last_popup_created or page_var == last_popup_created:
        return []
    return [
        "            # [PRE] popup 직후 본 page focus 회복",
        f"            {page_var}.bring_to_front()",
        f"            {page_var}.wait_for_timeout(200)",
    ]


def _emit_heal_trace_lines(step: dict, heal_stage: str, target) -> list[str]:
    """description 코멘트 + heal trace 코멘트. selector 가 바뀐 경우만 원본 노출."""
    out: list[str] = []
    desc = step.get("description", "")
    if desc:
        out.append(f"            # {desc}")
    if heal_stage != "none":
        scen_target = step.get("target", "")
        if scen_target and str(scen_target) != str(target):
            out.append(
                f"            # [HEALED via {heal_stage}] "
                f"original target: {scen_target!r}"
            )
        else:
            out.append(f"            # [HEALED via {heal_stage}]")
    return out


def _emit_pre_actions_lines(r, page_var: str) -> list[str]:
    """visibility healer 가 통과시킨 사전 액션 시퀀스 (hover / wait) 재현."""
    if r is None:
        return []
    out: list[str] = []
    for pre in (getattr(r, "pre_actions", None) or []):
        if not isinstance(pre, dict):
            continue
        pre_action = str(pre.get("action", "")).lower()
        pre_target = pre.get("target", "")
        if pre_action == "hover" and pre_target:
            out.append("            # [PRE] visibility heal — hover ancestor")
            out.append(
                f"            {page_var}.locator({json.dumps(str(pre_target))})"
                ".first.hover(timeout=5000)"
            )
            out.append(f"            {page_var}.wait_for_timeout(150)")
        elif pre_action == "wait" and pre_target:
            try:
                wait_ms = int(str(pre_target))
            except ValueError:
                wait_ms = 1000
            out.append("            # [PRE] visibility heal — size poll")
            out.append(f"            {page_var}.wait_for_timeout({wait_ms})")
    return out


def _wrap_popup_step(
    step_lines: list[str],
    popup_to: str,
    page_var: str,
    scenario: list[dict],
    idx: int,
) -> list[str]:
    """popup_to 가 박힌 step 을 ``with expect_popup()`` 블록으로 wrap.

    바로 다음 step 이 같은 popup 을 close 하면 settle 생략 (popup 의
    networkidle 미도달로 인한 timeout 회귀 차단).
    """
    out: list[str] = []
    out.append(f"            with {page_var}.expect_popup() as {popup_to}_info:")
    for sl in step_lines:
        # step_lines 는 이미 12-space base 들여쓰기. with 블록 내부는 +4 space.
        if sl.startswith("            "):
            out.append("    " + sl)
        else:
            out.append("            " + sl)
    out.append(f"            {popup_to} = {popup_to}_info.value")

    next_step = scenario[idx + 1] if idx + 1 < len(scenario) else None
    close_follows = (
        next_step is not None
        and str(next_step.get("action", "")).lower() == "close"
        and (next_step.get("page") or "page") == popup_to
    )
    if not close_follows:
        out.append(f"            _settle({popup_to})")
    return out


def _emit_step_body_lines(
    step_lines: list[str],
    popup_to,
    page_var: str,
    action: str,
    known_pages: set,
    scenario: list[dict],
    results: list,
    idx: int,
):
    """본 step 의 body emit + last_popup_created 갱신 반환.

    Returns:
        (lines, last_popup_created_or_None)
    """
    if popup_to and popup_to not in known_pages:
        body = _wrap_popup_step(step_lines, popup_to, page_var, scenario, idx)
        known_pages.add(popup_to)
        return body, popup_to

    body = list(step_lines)
    if action == "close":
        # close 액션은 page 자체를 닫으므로 settle / lookahead 모두 skip.
        return body, None

    # 일반 step — action 직후 networkidle 동적 대기 + 다음 step element 의
    # DOM attach lookahead. modal/dialog 처럼 비동기 trigger 가 next step
    # element 를 만드는 패턴 대응.
    body.append(f"            _settle({page_var})")
    _next = _peek_next_locator_code(scenario, results, idx, known_pages)
    if _next is not None:
        _next_page_var, _next_locator_code, _next_state = _next
        body.append(
            f"            try: {_next_locator_code}.wait_for(state={_next_state!r}, timeout=_step_wait_ms)"
        )
        body.append(_EXCEPT_PASS_LINE)
    return body, None


def generate_regression_test(
    scenario: list[dict],
    results: list[StepResult],
    output_dir: str,
) -> str | None:
    """
    모든 스텝이 성공(PASS/HEALED)한 경우,
    LLM 없이 독립 실행 가능한 Playwright 스크립트를 생성한다.
    실패 스텝이 있으면 생성하지 않고 None을 반환한다.

    구조: 헤더 boilerplate → step loop (resolve / pre_emit / step body) → footer.
    각 단계는 별도 헬퍼로 분리되어 본 함수는 흐름 제어만 담당 (cognitive
    complexity 96 → ≤15 정리, 2026-05-15).
    """
    if any(r.status == "FAIL" for r in results):
        log.info("[Regression] failed step present — skipping generation")
        return None

    needs_auth_imports = any(
        s.get("action", "").lower() == "auth_login" for s in scenario
    )
    lines = _build_header_lines(needs_auth_imports)

    # 팝업 page var 추적 — converter_ast 가 emit 한 step["page"] / step["popup_to"]
    # 를 보존해 회귀 .py 가 원본의 다중 탭 흐름을 재현하게 한다.
    known_pages: set[str] = {"page"}
    # 직전 step 이 popup 을 새로 띄웠다면 그 popup 의 var 이름을 기록.
    # 다음 step 이 *다른* page 대상이면 bring_to_front 로 focus 회복.
    last_popup_created: str | None = None
    for idx, step in enumerate(scenario):
        r = results[idx] if idx < len(results) else None
        action, target, value, heal_stage = _resolve_step_io(step, r)
        page_var = (step.get("page") or "page")
        popup_to = step.get("popup_to") or None

        lines.extend(_emit_popup_focus_lines(page_var, last_popup_created))
        last_popup_created = None
        lines.extend(_emit_heal_trace_lines(step, heal_stage, target))
        lines.extend(_emit_pre_actions_lines(r, page_var))

        locator_code = _target_to_playwright_code(target, page_var=page_var)
        step_lines = _emit_step_code(
            action, target, value, step, locator_code, page_var=page_var,
        )
        body, last_popup_created = _emit_step_body_lines(
            step_lines, popup_to, page_var, action, known_pages,
            scenario, results, idx,
        )
        lines.extend(body)
        lines.append("")

    lines.extend(_build_footer_lines())

    output_path = os.path.join(output_dir, "regression_test.py")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("[Regression] standalone test generation done: %s", output_path)
    return output_path


def _emit_step_code(
    action: str, target, value, step: dict, locator_code: str,
    page_var: str = "page",
) -> list[str]:
    """단일 step 을 Playwright 호출 라인 목록으로 변환한다.

    14대 DSL 액션 모두를 처리한다. 신규 5종(upload/drag/scroll/mock_*)도
    executor 의 실제 동작과 1:1 로 매핑되어 회귀 테스트가 동등하게 재현한다.

    page_var: 액션이 동작할 page 변수 (메인=page, popup=page1/page2/…).
    """
    handler = _ACTION_EMITTERS.get(action)
    if handler is None:
        return [f"            # [skip] 미지원 action: {action}"]
    return handler(target, value, step, locator_code, page_var)


def _emit_navigate(target, value, step, locator_code, page_var="page"):
    url = value or str(target)
    return [
        f"            {page_var}.goto({json.dumps(url)})",
        f'            {page_var}.wait_for_load_state("domcontentloaded")',
    ]


def _emit_wait(target, value, step, locator_code, page_var="page"):
    return [f"            {page_var}.wait_for_timeout({int(value or 1000)})"]


def _emit_click(target, value, step, locator_code, page_var="page"):
    # Playwright 의 ``Locator.click`` 자체가 actionable wait + auto-scroll +
    # 재시도를 포함. ``wait_for(state='visible')`` 를 앞에 두면 *viewport 안*
    # 가시성을 요구해 off-viewport 요소(visibility-healer 의 phase 1 scroll
    # 케이스) 가 timeout 으로 깨진다 (2026-05-13 사용자 보고, session 6802f8a6faef
    # step 17 — "모두의 AI 실험실").
    #
    # 옛 5s timeout 은 사이트 응답 지연에 너무 짧아 flaky FAIL → 15s 로 상향만
    # 하고 wait_for 제거. click 의 auto-wait/auto-scroll 에 모든 안전망 위임.
    #
    # 추가: _safe_click 으로 감싸 actionability 거부 시 JS dispatch 폴백 — 사용자
    # 녹화에선 통과했지만 회귀에선 닫히는 레이어 / transition 잔여물에 가려져
    # Playwright 의 hit-test 가 실패하는 케이스를 흡수 (executor 가 LLM 모드에서
    # 하던 폴백과 동일).
    return [f"            _safe_click({locator_code}, timeout=_action_timeout_ms)"]


def _emit_fill(target, value, step, locator_code, page_var="page"):
    # 한 글자씩 입력 + keyup dispatch + 자동완성 settle — executor _do_fill 1순위
    # 전략 완전 미러. 빠뜨릴 경우 한국형 검색 자동완성 사이트가 dropdown 을 못
    # 띄워 다음 step 의 추천어 click 이 5초 timeout 으로 깨짐 (2026-05-11 회귀).
    #
    # 단계 의미:
    #   1) fill("") — 이전 값 제거 (codegen 이 빈 fill 로 clear 의도 캡처하는 패턴).
    #   2) press_sequentially — 한 글자씩 native keydown/keyup 발사 (~80ms 간격).
    #      한 번에 set 하는 fill(v) 은 input 이벤트만 발사해 자동완성 listener 미트리거.
    #   3) JS evaluate — 추가 KeyboardEvent('keyup') dispatch. 일부 사이트 listener
    #      가 native 이벤트가 아닌 dispatchEvent 로만 ajax 를 트리거함. JS try/catch
    #      안에 두어 element detach 같은 race 도 swallow.
    #   4) wait_for_timeout(300) — 자동완성 비동기 응답 settle. debounce 기본 250ms
    #      + 짧은 네트워크 RTT 를 덮음. 다음 step 이 click 이면 click 의 5초 retry
    #      안에 dropdown 이 떠야 매칭.
    return [
        f"            {locator_code}.fill(\"\")",
        f"            {locator_code}.press_sequentially({json.dumps(str(value))}, delay=80)",
        f"            {locator_code}.evaluate(\"el => {{ try {{ el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true}})); }} catch(e) {{}} }}\")",
        f"            {page_var}.wait_for_timeout(300)",
    ]


def _emit_press(target, value, step, locator_code, page_var="page"):
    if not target:
        return [f"            {page_var}.keyboard.press({json.dumps(str(value))})"]
    return [f"            {locator_code}.press({json.dumps(str(value))})"]


def _emit_select(target, value, step, locator_code, page_var="page"):
    # executor _do_select 의 3-전략 미러 (positional → value= → label=).
    # 시나리오 value 가 옵션 value("01") 인지 라벨("본인 활용") 인지 generator
    # 시점에 알 수 없으므로 executor 가 PASS 시킨 것과 동일한 순서로 폴백.
    # label= 만 emit 하던 이전 구현은 옵션 value 인 케이스에서 항상 fail (2026-05-14).
    #
    # combobox.nth(N) 같은 위치 기반 selector 는 ajax 로 늦게 로드되는 select 가
    # 페이지에 자리잡기 전 select_option 이 호출되어 깨지던 회귀(2026-05-11 FLOW-USR-007
    # step 14) 가 있어 wait_for(attached) 도 유지.
    val_json = json.dumps(str(value))
    return [
        f"            _sel = {locator_code}",
        "            _sel.wait_for(state='attached', timeout=_action_timeout_ms)",
        "            _last_err = None",
        f"            for _kw in ({{}}, {{'value': {val_json}}}, {{'label': {val_json}}}):",
        "                try:",
        "                    if _kw: _sel.select_option(**_kw, timeout=5000)",
        f"                    else: _sel.select_option({val_json}, timeout=5000)",
        "                    _last_err = None",
        "                    break",
        "                except Exception as _e:",
        "                    _last_err = _e",
        "            if _last_err is not None:",
        "                raise _last_err",
    ]


def _emit_check(target, value, step, locator_code, page_var="page"):
    if str(value).lower() == "off":
        return [f"            {locator_code}.uncheck()"]
    return [f"            {locator_code}.check()"]


def _emit_hover(target, value, step, locator_code, page_var="page"):
    return [f"            {locator_code}.hover()"]


def _emit_upload(target, value, step, locator_code, page_var="page"):
    # 업로드 경로는 artifacts 기준 상대경로로 들어옴 — 회귀 테스트는 실행 위치
    # (artifacts 디렉토리)에서 그대로 set_input_files 한다.
    return [
        f"            {locator_code}.set_input_files({json.dumps(str(value))})"
    ]


def _emit_drag(target, value, step, locator_code, page_var="page"):
    target_locator = _target_to_playwright_code(value, page_var=page_var)
    return [
        f"            _src = {locator_code}",
        f"            _dst = {target_locator}",
        "            _src.drag_to(_dst, timeout=_action_timeout_ms)",
    ]


def _emit_scroll(target, value, step, locator_code, page_var="page"):
    return [f"            {locator_code}.scroll_into_view_if_needed(timeout=_action_timeout_ms)"]


def _emit_mock_status(target, value, step, locator_code, page_var="page"):
    pattern = json.dumps(str(target))
    status = int(str(value).strip())
    return [
        f"            {page_var}.route({pattern}, lambda r: r.fulfill(status={status}), times=1)",
    ]


def _emit_mock_data(target, value, step, locator_code, page_var="page"):
    pattern = json.dumps(str(target))
    if isinstance(value, (dict, list)):
        body = json.dumps(json.dumps(value, ensure_ascii=False))
    else:
        body = json.dumps(str(value))
    return [
        f"            {page_var}.route({pattern}, lambda r: r.fulfill(status=200, "
        f'content_type="application/json", body={body}), times=1)',
    ]


def _emit_verify(target, value, step, locator_code, page_var="page"):
    condition = str(step.get("condition", "")).strip().lower()
    if condition == "hidden":
        return [f"            assert not {locator_code}.is_visible()"]
    if condition == "disabled":
        return [f"            assert {locator_code}.is_disabled()"]
    if condition == "enabled":
        return [f"            assert {locator_code}.is_enabled()"]
    if condition == "checked":
        return [f"            assert {locator_code}.is_checked()"]
    if condition == "value":
        return [
            f"            assert {locator_code}.input_value() == {json.dumps(str(value))}"
        ]
    if condition in ("text", "contains_text", "contains") or (
        condition in ("", "visible") and value
    ):
        return [
            f"            _el = {locator_code}",
            "            _text = _el.inner_text() or _el.input_value()",
            f"            assert {json.dumps(str(value))} in _text",
        ]
    # url_contains / url_not_contains — target 무시, page.url 자체 검사. Tour Script
    # Generator 의 안전망 assert (errorMsg 패턴) 도 이쪽 분기. executor 와 의미 동일.
    if condition == "url_not_contains":
        return [
            f"            _url = {page_var}.url or \"\"",
            f"            assert {json.dumps(str(value))} not in _url, _url",
        ]
    if condition == "url_contains":
        return [
            f"            _url = {page_var}.url or \"\"",
            f"            assert {json.dumps(str(value))} in _url, _url",
        ]
    # min_text_length — 본문(보통 body) inner_text 길이 ≥ 임계치. 빈 화면/안내
    # 페이지 가드. executor 와 의미 동일.
    if condition == "min_text_length":
        try:
            threshold = int(str(value))
        except (TypeError, ValueError):
            threshold = 0
        return [
            f"            _body = ({locator_code}.inner_text(timeout=5000) or \"\").strip()",
            f"            assert len(_body) >= {threshold}, "
            f"f\"본문 텍스트 길이 {{len(_body)}} < {threshold}\"",
        ]
    return [f"            assert {locator_code}.is_visible()"]


def _emit_auth_login(target, value, step, locator_code, page_var="page"):
    """auth_login emitter — credential alias + mode 분기.

    회귀 스크립트는 zero_touch_qa.auth 모듈을 import 해 fixture 통합과
    동일한 흐름을 재현한다. resolve_credential / generate_totp_code 가
    이미 검증된 path 라 1:1 매핑.

    page_var: 메인 페이지 외 popup 에서 인증 흐름이 발생할 일은 거의 없지만,
    혹시 모를 케이스를 위해 인자 통일.
    """
    target_str = json.dumps(str(target))
    alias = json.dumps(str(value))
    p = page_var
    return [
        "            # auth_login (T-D / P0.1) — env var credential + form/totp/oauth",
        f"            _opts = parse_auth_target({target_str})",
        f"            _cred = resolve_credential({alias})",
        '            if _opts.mode == "form":',
        f"                _email = {p}.locator(_opts.email_field) if _opts.email_field else None",
        "                if _email is None or _email.count() == 0:",
        "                    for _sel in EMAIL_FIELD_CANDIDATES:",
        f"                        if {p}.locator(_sel).count() > 0:",
        f"                            _email = {p}.locator(_sel); break",
        f"                _pwd = {p}.locator(_opts.password_field) if _opts.password_field else None",
        "                if _pwd is None or _pwd.count() == 0:",
        "                    for _sel in PASSWORD_FIELD_CANDIDATES:",
        f"                        if {p}.locator(_sel).count() > 0:",
        f"                            _pwd = {p}.locator(_sel); break",
        f"                _submit = {p}.locator(_opts.submit) if _opts.submit else None",
        "                if _submit is None or _submit.count() == 0:",
        "                    for _sel in SUBMIT_BUTTON_CANDIDATES:",
        f"                        if {p}.locator(_sel).count() > 0:",
        f"                            _submit = {p}.locator(_sel); break",
        "                _email.first.fill(_cred.user, timeout=5000)",
        "                _pwd.first.fill(_cred.password, timeout=5000)",
        "                _submit.first.click(timeout=5000)",
        f'                {p}.wait_for_load_state("domcontentloaded", timeout=10000)',
        '            elif _opts.mode == "totp":',
        "                _code = generate_totp_code(_cred.totp_secret)",
        f"                _otp = {p}.locator(_opts.totp_field) if _opts.totp_field else None",
        "                if _otp is None or _otp.count() == 0:",
        "                    for _sel in TOTP_FIELD_CANDIDATES:",
        f"                        if {p}.locator(_sel).count() > 0:",
        f"                            _otp = {p}.locator(_sel); break",
        "                _otp.first.fill(_code, timeout=5000)",
    ]


def _emit_reset_state(target, value, step, locator_code, page_var="page"):
    """reset_state emitter — cookie / storage / indexeddb / all (+ permissions).

    executor `_execute_reset_state` 와 1:1 매핑. all 은 permissions 도 reset.
    """
    scope = str(value).strip().lower()
    out = [f"            # reset_state value={scope}"]
    if scope in ("cookie", "all"):
        out.append(f"            {page_var}.context.clear_cookies()")
    if scope == "all":
        out.extend([
            f"            try: {page_var}.context.clear_permissions()",
            _EXCEPT_PASS_LINE,
        ])
    if scope in ("storage", "all"):
        out.append(
            f"            {page_var}.evaluate(\"\"\"() => {{"
            " try { localStorage.clear(); } catch(e) {} "
            " try { sessionStorage.clear(); } catch(e) {} "
            "}\"\"\")"
        )
    if scope in ("indexeddb", "all"):
        out.append(
            f"            {page_var}.evaluate(\"\"\"async () => {{ "
            "if (!('indexedDB' in window) || !indexedDB.databases) return; "
            "try { const dbs = await indexedDB.databases(); "
            "await Promise.all(dbs.map(d => new Promise((res) => { "
            "if (!d.name) return res(); "
            "const r = indexedDB.deleteDatabase(d.name); "
            "r.onsuccess = r.onerror = r.onblocked = () => res(); }))); } "
            "catch(e) {} }\"\"\")"
        )
    return out


def _emit_dialog_choose(target, value, step, locator_code, page_var="page"):
    """dialog_choose emitter — 다음 dialog 의 one-shot 응답 등록.

    executor._execute_dialog_choose 와 의미 동일. target ∈ {alert, confirm, prompt, any},
    value ∈ {accept, dismiss, "<prompt 응답 텍스트>"}.
    """
    dt = str(target or "any").strip().lower()
    val = str(value or "dismiss")
    p = page_var
    return [
        f"            # dialog_choose target={dt} value={val!r}",
        f"            _choice = ({json.dumps(dt)}, {json.dumps(val)})",
        "            def _on_dlg(_d, _c=_choice):",
        "                _tgt, _val = _c",
        "                if _tgt != 'any' and _d.type != _tgt: return",
        "                if _val == 'accept': _d.accept()",
        "                elif _val == 'dismiss': _d.dismiss()",
        "                else: _d.accept(_val)",
        f"            {p}.once('dialog', _on_dlg)",
    ]


def _emit_storage_read(target, value, step, locator_code, page_var="page"):
    """storage_read emitter — local/session storage 의 키 존재/값 검증.

    executor._execute_storage_read 와 의미 동일. target 형식 ``<scope>:<key>``
    (scope ∈ local|session, 생략 시 local). value 빈 문자열 = 키 존재만 검증.
    """
    raw_target = str(target or "")
    expected = str(value or "")
    if ":" in raw_target:
        scope, key = raw_target.split(":", 1)
        scope = scope.strip().lower() or "local"
        key = key.strip()
    else:
        scope, key = "local", raw_target
    store = "localStorage" if scope == "local" else "sessionStorage"
    p = page_var
    lines = [
        f"            # storage_read {scope}:{key} expected={expected!r}",
        f"            _val = {p}.evaluate("
        f"f'(k) => {{ try {{ return {store}.getItem(k); }} catch(e) {{ return null; }} }}', "
        f"{json.dumps(key)})",
    ]
    if expected == "":
        lines.append(f"            assert _val is not None, f'storage {scope}:{key} 미존재'")
    else:
        lines.append(
            f"            assert _val == {json.dumps(expected)}, "
            f"f'storage {scope}:{key} 값 불일치 actual={{_val!r}}'"
        )
    return lines


def _emit_cookie_verify(target, value, step, locator_code, page_var="page"):
    """cookie_verify emitter — 특정 cookie 존재/값 검증.

    executor._execute_cookie_verify 와 의미 동일. target ``NAME`` 또는
    ``NAME@DOMAIN``. value 빈 문자열 = cookie 존재만 검증.
    """
    raw_target = str(target or "")
    expected = str(value or "")
    if "@" in raw_target:
        name, domain = raw_target.split("@", 1)
        name = name.strip()
        domain = domain.strip().lstrip(".")
    else:
        name, domain = raw_target, ""
    p = page_var
    lines = [
        f"            # cookie_verify {raw_target} expected={expected!r}",
        f"            _cookies = {p}.context.cookies()",
        f"            _matched = [c for c in _cookies if c.get('name') == {json.dumps(name)} "
        f"and (not {json.dumps(domain)} or (c.get('domain') or '').lstrip('.') == {json.dumps(domain)})]",
        f"            assert _matched, f'cookie {raw_target!r} 미존재 (전체 {{len(_cookies)}}개)'",
    ]
    if expected != "":
        lines.append(
            "            _vals = [c.get('value','') for c in _matched]"
        )
        lines.append(
            f"            assert {json.dumps(expected)} in _vals, "
            f"f'cookie {raw_target!r} 값 불일치 actual={{_vals!r}}'"
        )
    return lines


def _emit_performance(target, value, step, locator_code, page_var="page"):
    """performance emitter — page load time 임계 비교.

    executor._execute_performance 와 의미 동일. value=임계 ms (정수).
    """
    try:
        threshold = int(str(value or "").strip())
    except (TypeError, ValueError):
        threshold = 0
    p = page_var
    return [
        f"            # performance load time 임계 {threshold}ms",
        f"            _elapsed = {p}.evaluate("
        f"'() => {{ const t = performance.timing; "
        f"if (!t || !t.navigationStart || !t.loadEventEnd) return -1; "
        f"return t.loadEventEnd - t.navigationStart; }}')",
        "            assert isinstance(_elapsed, (int, float)) and _elapsed >= 0, "
        "'performance timing 미가용'",
        f"            assert _elapsed <= {threshold}, "
        f"f'load {{int(_elapsed)}}ms > 임계 {threshold}ms'",
    ]


def _emit_close(target, value, step, locator_code, page_var="page"):
    # 사용자가 녹화 중 명시적으로 닫은 탭/창 재현. 이미 닫혔어도 idempotent.
    return [
        f"            try: {page_var}.close()",
        _EXCEPT_PASS_LINE,
    ]


def _emit_visual_diff(target, value, step, locator_code, page_var="page"):
    """visual_diff emitter — golden 이미지와 viewport 픽셀 diff.

    PIL + golden 파일 필요 — 회귀 .py 가 외부 의존을 끌어들이지 않도록 주석
    skip 으로 emit. 사용자가 의도적으로 사용 시 별도 환경에서 실 측정.
    """
    return [
        f"            # [skip] visual_diff target={target!r} threshold={value!r} — "
        "PIL + golden 이미지 의존, 회귀 스크립트 standalone 보장 위해 주석 처리.",
        "            # 측정이 필요하면 zero_touch_qa executor 의 --mode execute 로 실 시나리오 실행.",
    ]


_ACTION_EMITTERS = {
    "navigate": _emit_navigate,
    "maps": _emit_navigate,
    "wait": _emit_wait,
    "click": _emit_click,
    "fill": _emit_fill,
    "press": _emit_press,
    "select": _emit_select,
    "check": _emit_check,
    "hover": _emit_hover,
    "upload": _emit_upload,
    "drag": _emit_drag,
    "scroll": _emit_scroll,
    "mock_status": _emit_mock_status,
    "mock_data": _emit_mock_data,
    "verify": _emit_verify,
    "auth_login": _emit_auth_login,
    "reset_state": _emit_reset_state,
    "dialog_choose": _emit_dialog_choose,
    "storage_read": _emit_storage_read,
    "cookie_verify": _emit_cookie_verify,
    "performance": _emit_performance,
    "visual_diff": _emit_visual_diff,
    "close": _emit_close,
}


def _peek_next_locator_code(scenario, results, idx, known_pages):
    """다음 step 의 wait 대상 locator 표현 + page_var + wait_state 반환.

    회귀 .py 가 현재 step 직후 *다음 step 의 element 가 등장할 때까지* 대기
    하면, modal/dialog 같이 직전 action 이 비동기로 만드는 element 도 안정적
    으로 잡힌다.

    wait_state 분기:
      - next step 에 pre_actions(hover ancestor) 가 있음 → ``"attached"`` —
        hover 가 일어나야 visible 되는 메뉴 케이스. visible 까지 강제하면
        hover 전이라 timeout.
      - 없음 → ``"visible"`` — modal/dialog 같이 직전 action 이 즉시 visible
        만드는 케이스. visible 까지 기다리면 후속 click 의 auto-wait 가 빠짐.

    Returns:
        (page_var, locator_code, wait_state) 또는 None.
    """
    next_idx = idx + 1
    if next_idx >= len(scenario):
        return None
    next_step = scenario[next_idx]
    next_action = (next_step.get("action", "") or "").lower()
    next_page_var = next_step.get("page") or "page"
    # lookahead 비대상 — navigate/maps/wait 은 자체로 페이지 전환·시간 대기,
    # popup_to 는 expect_popup 이 따로 보장, page var 미등록은 popup 이 아직
    # 등장 안 한 상태라 wait_for 자체가 의미 없음.
    if (
        next_action in ("navigate", "maps", "wait")
        or next_step.get("popup_to")
        or next_page_var not in known_pages
    ):
        return None
    next_r = results[next_idx] if next_idx < len(results) else None
    next_target = ""
    next_pre_actions: list = []
    if next_r is not None:
        next_target = (getattr(next_r, "stable_selector", "") or "") or (next_r.target or "")
        next_pre_actions = getattr(next_r, "pre_actions", None) or []
    if not next_target:
        next_target = next_step.get("target", "") or ""
    if not next_target:
        return None
    locator_code = _target_to_playwright_code(next_target, page_var=next_page_var)
    wait_state = "attached" if next_pre_actions else "visible"
    return next_page_var, locator_code, wait_state


def _target_to_playwright_code(target, page_var: str = "page") -> str:
    """DSL target 을 독립 실행 가능한 Playwright 코드 스니펫으로 변환한다.

    P0.1 #2 / T-C — ``>>`` 합성 chain (frame= / shadow= / role= / text= / ...
    + 후미 modifier nth=/has_text=) 을 모두 지원한다. resolver 의 chain 처리와
    동일 의미로 코드 스니펫을 누적한다.

    page_var: 액션이 동작할 page 변수 이름. 메인 페이지는 ``page``, 팝업 탭은
    ``page1`` / ``page2`` … (converter_ast 가 step['page'] 로 emit). 기본 ``page``
    로 popup 정보 없는 시나리오는 기존 동작 유지.
    """
    if not target:
        return f'{page_var}.locator("body")'

    if isinstance(target, dict):
        if target.get("role"):
            role = json.dumps(target["role"])
            name = json.dumps(target.get("name", ""))
            return f"{page_var}.get_by_role({role}, name={name}).first"
        if target.get("label"):
            return f"{page_var}.get_by_label({json.dumps(target['label'])}).first"
        if target.get("text"):
            return f"{page_var}.get_by_text({json.dumps(target['text'])}).first"
        if target.get("placeholder"):
            return f"{page_var}.get_by_placeholder({json.dumps(target['placeholder'])}).first"
        if target.get("testid"):
            return f"{page_var}.get_by_test_id({json.dumps(target['testid'])}).first"
        target = target.get("selector", str(target))

    t = str(target).strip()

    # 후미 modifier 분리 — base_str 과 분리해 처리.
    base_str, modifiers = _split_trailing_modifiers(t)

    if " >> " in base_str:
        return _chain_to_playwright_code(base_str, modifiers, page_var=page_var)

    # 단일 segment — modifier 가 있으면 .first 미부착 raw 로 emit (chain 경로와 동일).
    # ``.first.nth(N)`` 은 Playwright 의미상 빈 매치라 N≥1 에서 timeout → 회귀 실패.
    # locator_resolver._resolve_raw 와 같은 의도.
    snippet = _segment_to_playwright_code(
        base_str, root=page_var, in_chain=bool(modifiers)
    )
    return _apply_modifier_suffix(snippet, modifiers)


def _split_trailing_modifiers(t: str) -> tuple[str, list[tuple[str, str]]]:
    """``, nth=N`` / ``, has_text=T`` 후미 modifier 만 분리. resolver 의
    `_split_modifiers` 와 동일 의미. base 안의 콤마 (`role=link, name=뉴스`)
    는 보존."""
    parts = t.split(", ")
    mods: list[tuple[str, str]] = []
    while parts:
        last = parts[-1]
        if "=" not in last:
            break
        key, _, value = last.partition("=")
        if key.strip() not in ("nth", "has_text"):
            break
        mods.append((key.strip(), value.strip()))
        parts.pop()
    mods.reverse()
    return ", ".join(parts), mods


def _chain_to_playwright_code(base_str: str, modifiers, page_var: str = "page") -> str:
    """``>>`` chain 을 Playwright 메서드 chain 코드로 변환."""
    segments = [s.strip() for s in base_str.split(" >> ") if s.strip()]
    if not segments:
        return f'{page_var}.locator("body")'

    cur = page_var
    for seg in segments:
        if seg.startswith("frame="):
            sel = seg[len("frame="):].strip()
            cur = f"{cur}.frame_locator({json.dumps(sel)})"
            continue
        if _IFRAME_SELECTOR_RE.match(seg):
            # Codegen ``locator("iframe[...] >> #x")`` 형태가 14-DSL target 으로
            # 그대로 옮겨졌을 때 bare ``iframe[...]`` segment 가 들어온다.
            # locator_resolver._descend_segment 와 동일 의미로 frame_locator 진입.
            cur = f"{cur}.frame_locator({json.dumps(seg)})"
            continue
        if seg.startswith("shadow="):
            # Playwright 가 open shadow 자동 piercing — 일반 locator 로 충분.
            sel = seg[len("shadow="):].strip()
            cur = f"{cur}.locator({json.dumps(sel)})"
            continue
        cur = _segment_to_playwright_code(seg, root=cur, in_chain=True)

    return _apply_modifier_suffix(cur, modifiers)


def _segment_to_playwright_code(seg: str, *, root: str, in_chain: bool = False) -> str:
    """단일 segment (role=/text=/label=/placeholder=/testid=/CSS) 를 Playwright 코드로.

    ``in_chain=False`` 인 단일 segment 의 경우 ``.first`` 를 붙여 단일 element 로
    축약 (기존 동작 보존). chain 안에서는 후속 segment 가 추가될 수 있으므로
    ``.first`` 를 붙이지 않는다 — 마지막에 modifier 처리에서 단일 element 로 정리.
    """
    suffix = "" if in_chain else ".first"

    m = re.match(r"role=(.+?),\s*name=(.+)", seg)
    if m:
        role = json.dumps(m.group(1).strip())
        # name 끝의 ``, exact=true|false`` 는 modifier — name 안에 그대로 박히면
        # Playwright 가 그 전체 문자열을 accessible name 으로 매칭해 사이트의 실
        # 버튼을 못 찾고 timeout. executor 의 resolver 와 동일 의미로 분리.
        # 2026-05-11 FLOW-USR-007 사례 — ``name="검색, exact=true"`` 가 그대로
        # emit 되어 5초 timeout 으로 깨졌음.
        name_pure, exact = _split_name_exact(m.group(2))
        name = json.dumps(name_pure)
        if exact:
            return f"{root}.get_by_role({role}, name={name}, exact=True){suffix}"
        return f"{root}.get_by_role({role}, name={name}){suffix}"

    if seg.startswith("role="):
        role_only = seg[len("role="):].strip()
        if "," in role_only:
            role_only = role_only.split(",", 1)[0].strip()
        return f"{root}.get_by_role({json.dumps(role_only)}){suffix}"

    # locator_resolver 와 동일한 6개 semantic prefix. title= / alt= 누락 시
    # `page.locator("title=X")` 로 잘못 떨어져 CSS <title> 엘리먼트 매칭이 되어
    # 회귀 .py 가 step FAIL (사용자 보고 2026-05-13 fa81865a8b4c step 6).
    prefix_map = {
        "text=": "get_by_text",
        "label=": "get_by_label",
        "placeholder=": "get_by_placeholder",
        "testid=": "get_by_test_id",
        "title=": "get_by_title",
        "alt=": "get_by_alt_text",
    }
    for prefix, method in prefix_map.items():
        if seg.startswith(prefix):
            val = json.dumps(seg.replace(prefix, "", 1).strip())
            return f"{root}.{method}({val}){suffix}"

    return f"{root}.locator({json.dumps(seg)}){suffix}"


def _apply_modifier_suffix(code: str, modifiers) -> str:
    """nth=N / has_text=T 후미 modifier 를 Playwright .nth(N)/.filter(has_text=T) 로 변환.

    modifier 가 없으면 코드 끝이 이미 ``.first`` (단일 segment 경로) 인 상태로 반환.
    chain 경로에서 modifier 도 없는 경우 마지막에 ``.first`` 를 추가해 단일 element 로
    축약한다.
    """
    if not modifiers:
        # chain 경로의 무-모디파이어 케이스 — .first 미적용 상태이므로 추가.
        return code if code.endswith(".first") else f"{code}.first"

    out = code
    for key, value in modifiers:
        if key == "nth":
            try:
                idx = int(value)
            except ValueError:
                continue
            out = f"{out}.nth({idx})"
        elif key == "has_text":
            out = f"{out}.filter(has_text={json.dumps(value)})"
    return out
