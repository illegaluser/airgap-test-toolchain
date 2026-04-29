import json
import os
import re
import time
import logging

from .executor import StepResult

log = logging.getLogger(__name__)


def generate_regression_test(
    scenario: list[dict],
    results: list[StepResult],
    output_dir: str,
) -> str | None:
    """
    When every step succeeded (PASS/HEALED), generate a standalone
    Playwright script that needs no LLM.
    Returns None without generating anything if any step failed.
    """
    if any(r.status == "FAIL" for r in results):
        log.info("[Regression] failed step exists — skipping generation")
        return None

    needs_auth_imports = any(
        s.get("action", "").lower() == "auth_login" for s in scenario
    )

    lines = [
        '"""',
        "Auto-generated regression test from Zero-Touch QA scenario.",
        "Standalone Playwright script that runs without an LLM.",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        '"""',
        "from playwright.sync_api import sync_playwright",
    ]
    if needs_auth_imports:
        # auth_login must reuse zero_touch_qa.auth's credential lookup + TOTP
        # generation to be self-contained. Assumes the regression script runs
        # on the same PYTHONPATH as the zero_touch_qa package.
        # (Default assumption: from the artifacts/ location.)
        lines.append(
            "from zero_touch_qa.auth import (\n"
            "    EMAIL_FIELD_CANDIDATES, PASSWORD_FIELD_CANDIDATES,\n"
            "    SUBMIT_BUTTON_CANDIDATES, TOTP_FIELD_CANDIDATES,\n"
            "    generate_totp_code, parse_auth_target, resolve_credential,\n"
            ")"
        )
    lines.extend([
        "",
        "",
        "def test_regression():",
        '    with sync_playwright() as p:',
        "        browser = p.chromium.launch(headless=True)",
        "        context = browser.new_context(",
        '            viewport={"width": 1440, "height": 900},',
        '            locale="ko-KR",',
        "        )",
        "        page = context.new_page()",
        "        try:",
    ])

    for step in scenario:
        action = step["action"].lower()
        target = step.get("target", "")
        value = step.get("value", "")
        desc = step.get("description", "")

        if desc:
            lines.append(f"            # {desc}")

        locator_code = _target_to_playwright_code(target)
        lines.extend(_emit_step_code(action, target, value, step, locator_code))
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
    log.info("[Regression] standalone test generated: %s", output_path)
    return output_path


def _emit_step_code(
    action: str, target, value, step: dict, locator_code: str
) -> list[str]:
    """Convert a single step into a list of Playwright call lines.

    Handles all 14 DSL actions. The new five (upload/drag/scroll/mock_*) map
    1:1 to the executor's actual behavior so the regression test reproduces
    them identically.
    """
    handler = _ACTION_EMITTERS.get(action)
    if handler is None:
        return [f"            # [skip] unsupported action: {action}"]
    return handler(target, value, step, locator_code)


def _emit_navigate(target, value, step, locator_code):
    url = value or str(target)
    return [
        f"            page.goto({json.dumps(url)})",
        '            page.wait_for_load_state("domcontentloaded")',
    ]


def _emit_wait(target, value, step, locator_code):
    return [f"            page.wait_for_timeout({int(value or 1000)})"]


def _emit_click(target, value, step, locator_code):
    return [f"            {locator_code}.click(timeout=5000)"]


def _emit_fill(target, value, step, locator_code):
    return [f"            {locator_code}.fill({json.dumps(str(value))})"]


def _emit_press(target, value, step, locator_code):
    if not target:
        return [f"            page.keyboard.press({json.dumps(str(value))})"]
    return [f"            {locator_code}.press({json.dumps(str(value))})"]


def _emit_select(target, value, step, locator_code):
    return [
        f"            {locator_code}.select_option(label={json.dumps(str(value))})"
    ]


def _emit_check(target, value, step, locator_code):
    if str(value).lower() == "off":
        return [f"            {locator_code}.uncheck()"]
    return [f"            {locator_code}.check()"]


def _emit_hover(target, value, step, locator_code):
    return [f"            {locator_code}.hover()"]


def _emit_upload(target, value, step, locator_code):
    # The upload path arrives as a relative path under artifacts — the regression
    # test calls set_input_files from its own run location (the artifacts directory).
    return [
        f"            {locator_code}.set_input_files({json.dumps(str(value))})"
    ]


def _emit_drag(target, value, step, locator_code):
    target_locator = _target_to_playwright_code(value)
    return [
        f"            _src = {locator_code}",
        f"            _dst = {target_locator}",
        "            _src.drag_to(_dst, timeout=10000)",
    ]


def _emit_scroll(target, value, step, locator_code):
    return [f"            {locator_code}.scroll_into_view_if_needed(timeout=5000)"]


def _emit_mock_status(target, value, step, locator_code):
    pattern = json.dumps(str(target))
    status = int(str(value).strip())
    return [
        f"            page.route({pattern}, lambda r: r.fulfill(status={status}), times=1)",
    ]


def _emit_mock_data(target, value, step, locator_code):
    pattern = json.dumps(str(target))
    if isinstance(value, (dict, list)):
        body = json.dumps(json.dumps(value, ensure_ascii=False))
    else:
        body = json.dumps(str(value))
    return [
        f"            page.route({pattern}, lambda r: r.fulfill(status=200, "
        f'content_type="application/json", body={body}), times=1)',
    ]


def _emit_verify(target, value, step, locator_code):
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
    return [f"            assert {locator_code}.is_visible()"]


def _emit_auth_login(target, value, step, locator_code):
    """auth_login emitter — credential alias + mode branching.

    The regression script imports the zero_touch_qa.auth module and reproduces
    the same flow as the integrated fixture. Maps 1:1 because
    resolve_credential / generate_totp_code is already a validated path.
    """
    target_str = json.dumps(str(target))
    alias = json.dumps(str(value))
    return [
        "            # auth_login (T-D / P0.1) — env var credential + form/totp/oauth",
        f"            _opts = parse_auth_target({target_str})",
        f"            _cred = resolve_credential({alias})",
        '            if _opts.mode == "form":',
        "                _email = page.locator(_opts.email_field) if _opts.email_field else None",
        "                if _email is None or _email.count() == 0:",
        "                    for _sel in EMAIL_FIELD_CANDIDATES:",
        "                        if page.locator(_sel).count() > 0:",
        "                            _email = page.locator(_sel); break",
        "                _pwd = page.locator(_opts.password_field) if _opts.password_field else None",
        "                if _pwd is None or _pwd.count() == 0:",
        "                    for _sel in PASSWORD_FIELD_CANDIDATES:",
        "                        if page.locator(_sel).count() > 0:",
        "                            _pwd = page.locator(_sel); break",
        "                _submit = page.locator(_opts.submit) if _opts.submit else None",
        "                if _submit is None or _submit.count() == 0:",
        "                    for _sel in SUBMIT_BUTTON_CANDIDATES:",
        "                        if page.locator(_sel).count() > 0:",
        "                            _submit = page.locator(_sel); break",
        "                _email.first.fill(_cred.user, timeout=5000)",
        "                _pwd.first.fill(_cred.password, timeout=5000)",
        "                _submit.first.click(timeout=5000)",
        '                page.wait_for_load_state("domcontentloaded", timeout=10000)',
        '            elif _opts.mode == "totp":',
        "                _code = generate_totp_code(_cred.totp_secret)",
        "                _otp = page.locator(_opts.totp_field) if _opts.totp_field else None",
        "                if _otp is None or _otp.count() == 0:",
        "                    for _sel in TOTP_FIELD_CANDIDATES:",
        "                        if page.locator(_sel).count() > 0:",
        "                            _otp = page.locator(_sel); break",
        "                _otp.first.fill(_code, timeout=5000)",
    ]


def _emit_reset_state(target, value, step, locator_code):
    """reset_state emitter — cookie / storage / indexeddb / all (+ permissions).

    Maps 1:1 to executor `_execute_reset_state`. `all` also resets permissions.
    """
    scope = str(value).strip().lower()
    out = [f"            # reset_state value={scope}"]
    if scope in ("cookie", "all"):
        out.append("            page.context.clear_cookies()")
    if scope == "all":
        out.extend([
            "            try: page.context.clear_permissions()",
            "            except Exception: pass",
        ])
    if scope in ("storage", "all"):
        out.append(
            "            page.evaluate(\"\"\"() => {"
            " try { localStorage.clear(); } catch(e) {} "
            " try { sessionStorage.clear(); } catch(e) {} "
            "}\"\"\")"
        )
    if scope in ("indexeddb", "all"):
        out.append(
            "            page.evaluate(\"\"\"async () => { "
            "if (!('indexedDB' in window) || !indexedDB.databases) return; "
            "try { const dbs = await indexedDB.databases(); "
            "await Promise.all(dbs.map(d => new Promise((res) => { "
            "if (!d.name) return res(); "
            "const r = indexedDB.deleteDatabase(d.name); "
            "r.onsuccess = r.onerror = r.onblocked = () => res(); }))); } "
            "catch(e) {} }\"\"\")"
        )
    return out


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
}


def _target_to_playwright_code(target) -> str:
    """Convert a DSL target into a standalone Playwright code snippet.

    P0.1 #2 / T-C — supports ``>>`` composite chains (frame= / shadow= /
    role= / text= / ... + trailing modifiers nth=/has_text=). Builds the
    code snippet with the same semantics as the resolver's chain handling.
    """
    if not target:
        return 'page.locator("body")'

    if isinstance(target, dict):
        if target.get("role"):
            role = json.dumps(target["role"])
            name = json.dumps(target.get("name", ""))
            return f"page.get_by_role({role}, name={name}).first"
        if target.get("label"):
            return f"page.get_by_label({json.dumps(target['label'])}).first"
        if target.get("text"):
            return f"page.get_by_text({json.dumps(target['text'])}).first"
        if target.get("placeholder"):
            return f"page.get_by_placeholder({json.dumps(target['placeholder'])}).first"
        if target.get("testid"):
            return f"page.get_by_test_id({json.dumps(target['testid'])}).first"
        target = target.get("selector", str(target))

    t = str(target).strip()

    # Split trailing modifiers from base_str.
    base_str, modifiers = _split_trailing_modifiers(t)

    if " >> " in base_str:
        return _chain_to_playwright_code(base_str, modifiers)

    # Single segment — existing branch.
    snippet = _segment_to_playwright_code(base_str, root="page")
    return _apply_modifier_suffix(snippet, modifiers)


def _split_trailing_modifiers(t: str) -> tuple[str, list[tuple[str, str]]]:
    """Split only the trailing ``, nth=N`` / ``, has_text=T`` modifiers. Same
    semantics as the resolver's `_split_modifiers`. Preserves commas inside the
    base (e.g. `role=link, name=Top story`)."""
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


def _chain_to_playwright_code(base_str: str, modifiers) -> str:
    """Convert a ``>>`` chain into Playwright method-chain code."""
    segments = [s.strip() for s in base_str.split(" >> ") if s.strip()]
    if not segments:
        return 'page.locator("body")'

    cur = "page"
    for seg in segments:
        if seg.startswith("frame="):
            sel = seg[len("frame="):].strip()
            cur = f"{cur}.frame_locator({json.dumps(sel)})"
            continue
        if seg.startswith("shadow="):
            # Playwright auto-pierces open shadow — a regular locator is enough.
            sel = seg[len("shadow="):].strip()
            cur = f"{cur}.locator({json.dumps(sel)})"
            continue
        cur = _segment_to_playwright_code(seg, root=cur, in_chain=True)

    return _apply_modifier_suffix(cur, modifiers)


def _segment_to_playwright_code(seg: str, *, root: str, in_chain: bool = False) -> str:
    """Convert a single segment (role=/text=/label=/placeholder=/testid=/CSS) into Playwright code.

    For a single segment with ``in_chain=False``, append ``.first`` to narrow to a
    single element (preserves existing behavior). Inside a chain, do not append
    ``.first`` because more segments may follow — the modifier step at the end
    narrows to a single element.
    """
    suffix = "" if in_chain else ".first"

    m = re.match(r"role=(.+?),\s*name=(.+)", seg)
    if m:
        role = json.dumps(m.group(1).strip())
        name = json.dumps(m.group(2).strip())
        return f"{root}.get_by_role({role}, name={name}){suffix}"

    if seg.startswith("role="):
        role_only = seg[len("role="):].strip()
        if "," in role_only:
            role_only = role_only.split(",", 1)[0].strip()
        return f"{root}.get_by_role({json.dumps(role_only)}){suffix}"

    prefix_map = {
        "text=": "get_by_text",
        "label=": "get_by_label",
        "placeholder=": "get_by_placeholder",
        "testid=": "get_by_test_id",
    }
    for prefix, method in prefix_map.items():
        if seg.startswith(prefix):
            val = json.dumps(seg.replace(prefix, "", 1).strip())
            return f"{root}.{method}({val}){suffix}"

    return f"{root}.locator({json.dumps(seg)}){suffix}"


def _apply_modifier_suffix(code: str, modifiers) -> str:
    """Convert trailing nth=N / has_text=T modifiers into Playwright .nth(N) / .filter(has_text=T).

    If there are no modifiers, return as-is (the single-segment path already ends
    with ``.first``). For the chain path with no modifiers, append ``.first`` at
    the end to narrow to a single element.
    """
    if not modifiers:
        # Chain path with no modifiers — .first not yet applied, so add it.
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
