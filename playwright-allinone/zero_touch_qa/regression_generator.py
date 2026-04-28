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
    모든 스텝이 성공(PASS/HEALED)한 경우,
    LLM 없이 독립 실행 가능한 Playwright 스크립트를 생성한다.
    실패 스텝이 있으면 생성하지 않고 None을 반환한다.
    """
    if any(r.status == "FAIL" for r in results):
        log.info("[Regression] 실패 스텝 존재 — 생성 건너뜀")
        return None

    lines = [
        '"""',
        "Auto-generated regression test from Zero-Touch QA scenario.",
        "LLM 없이 독립 실행 가능한 Playwright 스크립트.",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        '"""',
        "from playwright.sync_api import sync_playwright",
        "",
        "",
        "def test_regression():",
        '    with sync_playwright() as p:',
        "        browser = p.chromium.launch(headless=True)",
        '        page = browser.new_page(viewport={"width": 1440, "height": 900})',
        "        try:",
    ]

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
    action: str, target, value, step: dict, locator_code: str
) -> list[str]:
    """단일 step 을 Playwright 호출 라인 목록으로 변환한다.

    14대 DSL 액션 모두를 처리한다. 신규 5종(upload/drag/scroll/mock_*)도
    executor 의 실제 동작과 1:1 로 매핑되어 회귀 테스트가 동등하게 재현한다.
    """
    handler = _ACTION_EMITTERS.get(action)
    if handler is None:
        return [f"            # [skip] 미지원 action: {action}"]
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
    # 업로드 경로는 artifacts 기준 상대경로로 들어옴 — 회귀 테스트는 실행 위치
    # (artifacts 디렉토리)에서 그대로 set_input_files 한다.
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
}


def _target_to_playwright_code(target) -> str:
    """DSL target을 독립 실행 가능한 Playwright 코드 스니펫으로 변환한다."""
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

    # role=button, name=로그인
    m = re.match(r"role=(.+?),\s*name=(.+)", t)
    if m:
        role = json.dumps(m.group(1).strip())
        name = json.dumps(m.group(2).strip())
        return f"page.get_by_role({role}, name={name}).first"

    prefix_map = {
        "text=": "page.get_by_text",
        "label=": "page.get_by_label",
        "placeholder=": "page.get_by_placeholder",
        "testid=": "page.get_by_test_id",
    }
    for prefix, method in prefix_map.items():
        if t.startswith(prefix):
            val = json.dumps(t.replace(prefix, "", 1).strip())
            return f"{method}({val}).first"

    return f"page.locator({json.dumps(t)}).first"
