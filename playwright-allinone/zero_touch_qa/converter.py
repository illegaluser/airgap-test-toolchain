import ast
import json
import os
import re
import logging

log = logging.getLogger(__name__)


def convert_playwright_to_dsl(file_path: str, output_dir: str) -> list[dict]:
    """
    Playwright codegen이 생성한 Python 스크립트를 파싱하여
    14대 DSL scenario.json으로 변환한다.

    매핑:
      9대 (Sprint 2 이전): navigate, wait, click, fill, press, select, check, hover, verify
      신규 5대 (Sprint 4C):
        - upload      ← page.locator(...).set_input_files("path")
        - drag        ← page.locator(src).drag_to(page.locator(dst))
        - scroll      ← page.locator(...).scroll_into_view_if_needed()
        - mock_status ← page.route("PATTERN", lambda r: r.fulfill(status=NN))
        - mock_data   ← page.route("PATTERN", lambda r: r.fulfill(... body=...))

    사용법:
      playwright codegen https://target-app.com --output recorded.py
      python3 -m zero_touch_qa --mode convert --file recorded.py
    """
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    scenario = []
    step_num = 0

    skip_keywords = (
        "import ", "from ", "def ", "with ", "browser", "context",
        "try:", "finally:", "if __name__", "print(", "# ---",
        '"""', "'''",
    )

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if any(line.startswith(k) for k in skip_keywords):
            continue
        # codegen 은 `with page.expect_popup() as page1_info` → `page1.click(...)` 식으로
        # popup/새 탭 액션을 page1, page2, … 변수에 바인딩한다. executor 는 매 스텝 후
        # context.pages 변화로 활성 page 를 자동 전환하므로(executor.py:166-201) 시나리오는
        # 단일 page 시퀀스로 평탄화해도 동작이 일치한다. 정규화 안 하면 popup 탭 액션이
        # 통째로 누락된다.
        line = re.sub(r"\bpage\d+\.", "page.", line)
        if not (line.startswith("page.") or line.startswith("expect(")):
            continue

        step = _parse_playwright_line(line)
        if step:
            step_num += 1
            step["step"] = step_num
            step.setdefault("fallback_targets", [])
            scenario.append(step)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "scenario.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)

    log.info("[Convert] %s -> %s (%d스텝 변환)", file_path, output_path, step_num)
    return scenario


def _parse_playwright_line(line: str) -> dict | None:
    """단일 Playwright 코드 라인을 DSL 스텝으로 변환한다."""

    # navigate
    m = re.search(r'page\.goto\(["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "navigate", "target": "", "value": m.group(1),
            "description": f"{m.group(1)}로 이동",
        }

    # wait
    m = re.search(r'page\.wait_for_timeout\((\d+)\)', line)
    if m:
        return {
            "action": "wait", "target": "", "value": m.group(1),
            "description": f"{m.group(1)}ms 대기",
        }

    # wait_for_load_state / wait_for_url 은 navigate에 부수적
    if "wait_for_load_state" in line or "wait_for_url" in line:
        return None

    # mock_status / mock_data — page.route("PATTERN", lambda r: r.fulfill(...))
    # 우선순위: 다른 액션과 토큰 충돌이 없으므로 page.locator 매칭 전에 처리.
    if "page.route(" in line and "fulfill" in line:
        mock_step = _parse_mock_route(line)
        if mock_step is not None:
            return mock_step

    target = _extract_target(line)

    # fill
    m = re.search(r'\.fill\(["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "fill", "target": target, "value": m.group(1),
            "description": f"'{m.group(1)}' 입력",
        }

    # press
    m = re.search(r'\.press\(["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "press", "target": target, "value": m.group(1),
            "description": f"{m.group(1)} 키 입력",
        }

    # select_option
    m = re.search(r'\.select_option\((?:label=)?["\'](.+?)["\']\)', line)
    if m:
        return {
            "action": "select", "target": target, "value": m.group(1),
            "description": f"'{m.group(1)}' 선택",
        }

    # check / uncheck
    if ".uncheck()" in line:
        return {
            "action": "check", "target": target, "value": "off",
            "description": "체크 해제",
        }
    if ".check()" in line:
        return {
            "action": "check", "target": target, "value": "on",
            "description": "체크",
        }

    # hover
    if ".hover()" in line:
        return {
            "action": "hover", "target": target, "value": "",
            "description": "마우스 호버",
        }

    # upload — set_input_files("path") 또는 set_input_files(["a", "b"]) (첫 항목 채택)
    m = re.search(r'\.set_input_files\(\s*\[\s*["\'](.+?)["\']', line)
    if m:
        return {
            "action": "upload", "target": target, "value": m.group(1),
            "description": f"'{m.group(1)}' 파일 업로드",
        }
    m = re.search(r'\.set_input_files\(\s*["\'](.+?)["\']\s*\)', line)
    if m:
        return {
            "action": "upload", "target": target, "value": m.group(1),
            "description": f"'{m.group(1)}' 파일 업로드",
        }

    # drag — page.locator(src).drag_to(page.locator(dst)) 형태
    if ".drag_to(" in line:
        dst_target = _extract_drag_destination(line)
        if dst_target:
            return {
                "action": "drag", "target": target, "value": dst_target,
                "description": "드래그 앤 드롭",
            }

    # scroll — scroll_into_view_if_needed
    if "scroll_into_view_if_needed" in line:
        return {
            "action": "scroll", "target": target, "value": "into_view",
            "description": "요소 위치로 스크롤",
        }

    # click (다른 액션 매칭 후 최후에 체크)
    if ".click(" in line or line.endswith(".click()"):
        return {
            "action": "click", "target": target, "value": "",
            "description": "클릭",
        }

    # expect → verify
    m = re.search(r'expect\((.+?)\)\.to_have_text\(["\'](.+?)["\']\)', line)
    if m:
        verify_target = _extract_target(m.group(1))
        return {
            "action": "verify", "target": verify_target, "value": m.group(2),
            "description": f"텍스트 '{m.group(2)}' 확인",
        }

    m = re.search(r'expect\((.+?)\)\.to_be_visible', line)
    if m:
        verify_target = _extract_target(m.group(1))
        return {
            "action": "verify", "target": verify_target, "value": "",
            "description": "요소 표시 확인",
        }

    return None


def _extract_target(line: str) -> str:
    """Playwright 로케이터 코드에서 DSL target 문자열을 추출한다."""

    # get_by_role("button", name="로그인")
    m = re.search(
        r'get_by_role\(["\'](.+?)["\'],\s*name=["\'](.+?)["\'](?:\s*,[^)]*)?\)',
        line,
    )
    if m:
        return f"role={m.group(1)}, name={m.group(2)}"

    # get_by_role("heading") (name 없음)
    m = re.search(r'get_by_role\(["\'](.+?)["\']\)', line)
    if m and "name=" not in line.split("get_by_role")[1].split(")")[0]:
        return f"role={m.group(1)}"

    # get_by_label
    m = re.search(r'get_by_label\(["\'](.+?)["\']\)', line)
    if m:
        return f"label={m.group(1)}"

    # get_by_text
    m = re.search(r'get_by_text\(["\'](.+?)["\']\)', line)
    if m:
        return f"text={m.group(1)}"

    # get_by_placeholder
    m = re.search(r'get_by_placeholder\(["\'](.+?)["\']\)', line)
    if m:
        return f"placeholder={m.group(1)}"

    # get_by_test_id
    m = re.search(r'get_by_test_id\(["\'](.+?)["\']\)', line)
    if m:
        return f"testid={m.group(1)}"

    # page.locator("css-selector")
    m = re.search(r'page\.locator\(["\'](.+?)["\']\)', line)
    if m:
        return m.group(1)

    return ""


def _extract_drag_destination(line: str) -> str:
    """`.drag_to(<expr>)` 의 인자에서 dst locator 표현식을 추출하여 DSL target 으로 변환."""
    idx = line.find(".drag_to(")
    if idx < 0:
        return ""
    # `(` 다음 위치
    start = idx + len(".drag_to(")
    depth = 1
    i = start
    while i < len(line) and depth > 0:
        c = line[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    inner = line[start:i]
    # inner 는 통상 `page.locator("...")` / `page.get_by_role("...", name="...")` 등
    # _extract_target 이 동일 패턴을 처리하므로 위임. timeout=, force= 같은 옵션 인자는
    # 첫 번째 표현식 뒤에 콤마로 따라오는데, _extract_target 의 정규식들은 첫 매칭만
    # 잡으므로 그대로 통과.
    return _extract_target(inner)


def _parse_mock_route(line: str) -> dict | None:
    """`page.route("PATTERN", lambda r: r.fulfill(...))` 형태를 mock_status/mock_data 로 변환."""
    m_pat = re.search(r'page\.route\(\s*["\'](.+?)["\']', line)
    if not m_pat:
        return None
    pattern = m_pat.group(1)

    # body=... 가 있으면 mock_data, 없고 status=NN 만 있으면 mock_status.
    m_body = re.search(r'body\s*=\s*([^,)]+(?:\([^)]*\))?[^,)]*)', line)
    if m_body:
        body_expr = m_body.group(1).strip()
        # 따옴표 문자열이면 Python escape 해제 후 value 로.
        # `body="{\"items\":[]}"` → `{"items":[]}` 로 평탄화해야 regression_generator
        # emitter (`json.dumps(str(value))`) 와 mock 라우트 fulfill body 가 1:1 일관.
        body_value = body_expr
        if re.match(r'^["\'].*["\']$', body_expr):
            try:
                body_value = ast.literal_eval(body_expr)
            except (ValueError, SyntaxError):
                body_value = body_expr.strip("\"'")
        return {
            "action": "mock_data", "target": pattern, "value": body_value,
            "description": f"{pattern} 응답 본문 모킹",
        }

    m_status = re.search(r'status\s*=\s*(\d+)', line)
    if m_status:
        return {
            "action": "mock_status", "target": pattern, "value": m_status.group(1),
            "description": f"{pattern} 응답 상태 {m_status.group(1)} 모킹",
        }
    return None
