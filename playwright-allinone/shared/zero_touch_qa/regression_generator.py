import json
import os
import re
import time
import logging

from .executor import StepResult
# name=<텍스트>, exact=true 같은 후미 옵션을 분리해 ``exact=`` kwarg 로 emit 하기
# 위해 executor 의 resolver 와 동일 헬퍼 재사용. 자체 정의하면 두 곳에서 정규식이
# 어긋날 위험.
from .locator_resolver import _split_name_exact

log = logging.getLogger(__name__)


def generate_regression_test(
    scenario: list[dict],
    results: list[StepResult],
    output_dir: str,
) -> str | None:
    """
    모든 스텝이 성공(PASS/HEALED)한 경우,
    LLM 없이 독립 실행 가능한 Playwright 스크립트를 생성한다.
    실패 스텝이 있으면 생성하지 않고 None을 반환한다.
    """
    if any(r.status == "FAIL" for r in results):
        log.info("[Regression] 실패 스텝 존재 — 생성 건너뜀")
        return None

    needs_auth_imports = any(
        s.get("action", "").lower() == "auth_login" for s in scenario
    )

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
        "        page = context.new_page()",
        "        try:",
    ])

    # 팝업 page var 추적 — converter_ast 가 emit 한 step["page"] / step["popup_to"]
    # 를 보존해 회귀 .py 가 원본의 다중 탭 흐름을 재현하게 한다. popup_to 가 박힌
    # step 은 ``with <page_var>.expect_popup() as <popup_to>_info:`` 로 wrap.
    known_pages: set[str] = {"page"}
    for idx, step in enumerate(scenario):
        r = results[idx] if idx < len(results) else None

        # Healing-aware override (2026-05-11 수정) — fallback / alternative /
        # local / dify 단계가 통과시킨 *실제 selector / action / value* 를
        # 우선 채택해 회귀 .py 가 "녹화 시점 fragile selector" 가 아닌
        # "최종 통과 selector" 를 들고 나가게 한다. r 값이 비어 있으면 원본
        # scenario 값으로 fallback (PASS 그리고 navigate/wait 처럼 target 이
        # 본래 빈 케이스 모두 동일하게 처리).
        scen_action = step["action"].lower()
        scen_target = step.get("target", "")
        scen_value = step.get("value", "")
        if r is not None:
            action = ((r.action or scen_action) or "").lower()
            target = r.target if r.target else scen_target
            # scenario value 가 dict/list (mock_data) 면 보존 — StepResult.value 는
            # 항상 str 이라 직렬화 손실이 발생함.
            if isinstance(scen_value, (dict, list)):
                value = scen_value
            else:
                value = r.value if r.value else scen_value
            heal_stage = (r.heal_stage or "none")
        else:
            action = scen_action
            target = scen_target
            value = scen_value
            heal_stage = "none"

        # 액션이 동작할 page 변수 결정. converter_ast 가 step["page"] 로 박은 값.
        # 평탄 시나리오(키 없음/legacy)는 자동으로 "page" 로 fallback.
        page_var = (step.get("page") or "page")
        popup_to = step.get("popup_to") or None

        desc = step.get("description", "")
        if desc:
            lines.append(f"            # {desc}")
        # heal trace 주석 — 운영자가 회귀 .py 를 손볼 때 어디가 fragile 했는지
        # 단서가 된다. selector 가 실제로 바뀐 경우에만 원본을 함께 노출.
        if heal_stage != "none":
            if scen_target and str(scen_target) != str(target):
                lines.append(
                    f"            # [HEALED via {heal_stage}] "
                    f"original target: {scen_target!r}"
                )
            else:
                lines.append(f"            # [HEALED via {heal_stage}]")

        # visibility healer 가 통과시킨 사전 액션 시퀀스 — 본 스텝 *앞에* 그대로
        # emit 해 같은 환경에서 같은 통과 시퀀스를 재현. Replay UI 의 raw wrapper
        # 가 healing 안전망을 매번 다시 돌릴 필요 없음 (사용자 지적 2026-05-11).
        if r is not None:
            for pre in (getattr(r, "pre_actions", None) or []):
                if not isinstance(pre, dict):
                    continue
                pre_action = str(pre.get("action", "")).lower()
                pre_target = pre.get("target", "")
                if pre_action == "hover" and pre_target:
                    lines.append(
                        f"            # [PRE] visibility heal — hover ancestor"
                    )
                    lines.append(
                        f"            {page_var}.locator({json.dumps(str(pre_target))})"
                        f".first.hover(timeout=1500)"
                    )
                    lines.append(f"            {page_var}.wait_for_timeout(150)")
                elif pre_action == "wait" and pre_target:
                    try:
                        wait_ms = int(str(pre_target))
                    except ValueError:
                        wait_ms = 1000
                    lines.append(
                        f"            # [PRE] visibility heal — size poll"
                    )
                    lines.append(f"            {page_var}.wait_for_timeout({wait_ms})")

        locator_code = _target_to_playwright_code(target, page_var=page_var)
        step_lines = _emit_step_code(action, target, value, step, locator_code, page_var=page_var)
        if popup_to and popup_to not in known_pages:
            # popup_to 가 박힌 step: 클릭 등이 새 page 를 트리거.
            # ``with <page_var>.expect_popup() as <popup_to>_info:`` 로 wrap,
            # 본 step 라인을 4 space 추가 들여쓰기, 직후 ``<popup_to> = ..._info.value``.
            lines.append(
                f"            with {page_var}.expect_popup() as {popup_to}_info:"
            )
            for sl in step_lines:
                # step_lines 는 이미 12-space base 들여쓰기. with 블록 내부는 +4 space.
                if sl.startswith("            "):
                    lines.append("    " + sl)
                else:
                    lines.append("            " + sl)
            lines.append(f"            {popup_to} = {popup_to}_info.value")
            known_pages.add(popup_to)
        else:
            lines.extend(step_lines)
        lines.append("")

    lines.extend([
        "        finally:",
        "            context.close()",
        "            browser.close()",
        "",
        "",
        'if __name__ == "__main__":',
        "    test_regression()",
        '    print("Regression test passed.")',
        "",
    ])

    output_path = os.path.join(output_dir, "regression_test.py")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("[Regression] 독립 테스트 생성 완료: %s", output_path)
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
    return [f"            {locator_code}.click(timeout=5000)"]


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
    # ``combobox.nth(N)`` 같은 위치 기반 selector 는 ajax 로 늦게 로드되는 select
    # 가 페이지에 *충분히 자리잡기 전* 에 select_option 이 호출되어 30s timeout
    # 으로 깨지던 회귀 (2026-05-11 FLOW-USR-007 step 14). 명시적 wait_for(attached)
    # 로 element 가 DOM 에 자리잡을 때까지 대기 후 select_option 호출.
    val_json = json.dumps(str(value))
    return [
        f"            _sel = {locator_code}",
        f"            _sel.wait_for(state='attached', timeout=15000)",
        f"            _sel.select_option(label={val_json})",
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
        "            _src.drag_to(_dst, timeout=10000)",
    ]


def _emit_scroll(target, value, step, locator_code, page_var="page"):
    return [f"            {locator_code}.scroll_into_view_if_needed(timeout=5000)"]


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
            "            except Exception: pass",
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
        f"            def _on_dlg(_d, _c=_choice):",
        f"                _tgt, _val = _c",
        f"                if _tgt != 'any' and _d.type != _tgt: return",
        f"                if _val == 'accept': _d.accept()",
        f"                elif _val == 'dismiss': _d.dismiss()",
        f"                else: _d.accept(_val)",
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
            f"            _vals = [c.get('value','') for c in _matched]"
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
        f"            assert isinstance(_elapsed, (int, float)) and _elapsed >= 0, "
        f"'performance timing 미가용'",
        f"            assert _elapsed <= {threshold}, "
        f"f'load {{int(_elapsed)}}ms > 임계 {threshold}ms'",
    ]


def _emit_visual_diff(target, value, step, locator_code, page_var="page"):
    """visual_diff emitter — golden 이미지와 viewport 픽셀 diff.

    PIL + golden 파일 필요 — 회귀 .py 가 외부 의존을 끌어들이지 않도록 주석
    skip 으로 emit. 사용자가 의도적으로 사용 시 별도 환경에서 실 측정.
    """
    return [
        f"            # [skip] visual_diff target={target!r} threshold={value!r} — "
        f"PIL + golden 이미지 의존, 회귀 스크립트 standalone 보장 위해 주석 처리.",
        f"            # 측정이 필요하면 zero_touch_qa executor 의 --mode execute 로 실 시나리오 실행.",
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
}


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
