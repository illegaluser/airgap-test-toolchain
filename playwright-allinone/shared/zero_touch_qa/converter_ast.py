"""AST 기반 Playwright codegen → 14-DSL 변환기 (T-A / P0.4 본체).

설계: docs/PLAN_PRODUCTION_READINESS.md §"T-A — converter AST 화"

기존 [converter.py](converter.py) 의 line-based regex 가 가지는 한계 해소:
- popup 탭 변수 (page1, page2, …) 의 액션 누락
- ``.nth(N)`` / ``.first`` / ``.filter(has_text=...)`` 정보 손실
- ``page.locator(...).locator(...)`` nested chain 평탄화 불가
- ``page.frame_locator(...).get_by_role(...)`` chain 손실

본 모듈은 ``ast.parse`` 로 정확 파싱한 뒤 ``def run(playwright)`` body 를 순회
하면서:
- page-like 변수 스코프 추적 (popup chain 포함)
- 액션 호출의 receiver chain 을 ``.nth/.first/.filter/locator/frame_locator``
  포함하여 14-DSL ``target`` 문자열로 평탄화
- 14-DSL 액션 14 개 모두 처리

비표준 패턴 (lambda, 변수 별칭, dynamic dispatch) 만나면 ``CodegenAstError``
발생 — 호출 측은 line-based fallback 으로 graceful degrade.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
from typing import Optional

from .step_kind import classify_step_kind

log = logging.getLogger(__name__)


class CodegenAstError(RuntimeError):
    """AST 변환 단계의 명시적 에러. 호출 측 line fallback 트리거."""


# Tour Script Generator (recording_service/server.py:_format_tour_steps_block) 가
# URL 마다 박는 안전망 assert 2종. 한국형 엔터프라이즈 사이트가 비로그인 접근에
# native alert 대신 `?errorMsg=...` 쿼리로 안내 페이지로 redirect 하는 패턴을
# 잡기 위함 (PLAN_RECORDING_UI_IMPROVEMENTS.md R10). 사용자에게 노출되는 step
# description 은 이 안전망의 의도가 드러나도록 분기.
_REDIRECT_MSG_KEYS = ("errorMsg", "error_msg", "msg")


def _describe_url_check(needle: str, is_not_in: bool) -> str:
    """assert "X" (not) in page.url 의 step description 생성.

    needle 이 안전망용 redirect 메시지 키이면 의도를 풀어 쓴 문구로 노출.
    그 외 일반 케이스는 단순 한국어 문장.
    """
    if is_not_in and needle in _REDIRECT_MSG_KEYS:
        return f"오류 페이지로 튕기지 않았는지 확인 (URL 에 '{needle}' 없음)"
    if is_not_in:
        return f"URL 에 '{needle}' 이(가) 없는지 확인"
    return f"URL 에 '{needle}' 이(가) 있는지 확인"


def _describe_min_text_length(selector: str, threshold: int) -> str:
    """assert len(page.inner_text("X")) >= N 의 step description 생성.

    selector 가 body 이고 임계치가 작은(<=100) 경우는 Tour Script Generator 의
    "빈 화면 가드" 패턴이라 의도를 풀어 쓴다. 그 외는 selector 그대로 노출.
    """
    if selector == "body" and threshold <= 100:
        return f"빈 화면이 아닌지 확인 (본문 {threshold}자 이상)"
    return f"'{selector}' 영역 텍스트가 {threshold}자 이상인지 확인"


def convert_via_ast(file_path: str, output_dir: str) -> list[dict]:
    """codegen .py 파일을 AST 로 파싱해 14-DSL 시나리오로 변환.

    Args:
        file_path: codegen 출력 .py 파일 절대 경로
        output_dir: scenario.json 을 저장할 디렉토리

    Returns:
        14-DSL 스텝 list. 각 스텝은 step/action/target/value/description/fallback_targets.

    Raises:
        FileNotFoundError: 입력 파일 없음
        CodegenAstError: AST 파싱/변환 실패 (line fallback 트리거 신호)
    """
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise CodegenAstError(f"AST 파싱 실패: {e}") from e

    converter = _AstConverter()
    converter.visit(tree)

    scenario = converter.steps
    # 임시 마커 제거 — 직렬화 전 cleanup. _pending_popup_info 같은 internal 키는
    # popup 트리거 ↔ promoted page var 매칭에만 쓰이고 scenario.json 으로 흘러
    # 나가면 안 된다. assign 으로 resolve 못 한 marker 도 안전 폐기.
    for s in scenario:
        for k in [k for k in s if k.startswith("_")]:
            del s[k]
    # 의도 분류 — auxiliary 로 식별된 step 에만 ``kind`` 를 명시. terminal 은
    # default 라 누락 (executor 가 기본값으로 처리, scenario.json 군더더기 방지).
    for s in scenario:
        if "kind" in s:
            continue
        kind = classify_step_kind(s.get("action", ""), s.get("target", ""))
        if kind != "terminal":
            s["kind"] = kind
    # 동일 accessible name 의 wrapper button + inner link 같은 중복 click 압축.
    # codegen 이 한 번의 사용자 클릭을 outer/inner 양쪽으로 emit 하는 케이스 회피.
    scenario = _dedupe_consecutive_clicks(scenario)
    # 한글 IME 사이트에서 codegen 이 emit 하는 노이즈 step (CapsLock toggle /
    # Unidentified key / 빈 fill→non-empty fill) 정리.
    scenario = _strip_ime_noise(scenario)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "scenario.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)

    log.info(
        "[Convert/AST] %s -> %s (%d스텝 변환)",
        file_path, output_path, len(scenario),
    )
    return scenario


# ─────────────────────────────────────────────────────────────────────────
# Post-processing — 중복 click 압축
# ─────────────────────────────────────────────────────────────────────────


def _normalized_click_identity(step: dict) -> Optional[str]:
    """click step 의 동일성 비교 키. 다른 step 또는 비교 불가면 None.

    role-locator (``role=X, name=Y[, exact=true]``) 는 name 부분을 추출해
    공백 정규화 + 인접 중복 토큰 압축. 그 외 selector 는 raw 문자열 사용.
    """
    if step.get("action") != "click":
        return None
    target = str(step.get("target") or "").strip()
    if not target:
        return None
    if target.startswith("role="):
        # name=...[, exact=...] 의 name 만 뽑아낸다.
        m = re.match(r"role=(?P<role>[^,]+),\s*name=(?P<name>.*?)(?:,\s*exact=\w+)?$", target)
        if not m:
            return target
        name = m.group("name").strip()
        # 공백/개행 정규화
        name = re.sub(r"\s+", " ", name)
        # 인접 중복 토큰 압축 — "페르소나 ChatBot 페르소나 ChatBot" → "페르소나 ChatBot"
        toks = name.split(" ")
        n = len(toks)
        for k in range(n // 2, 0, -1):
            if toks[:k] == toks[k:2 * k] and 2 * k == n:
                toks = toks[:k]
                break
        return f"name={' '.join(toks)}"
    return target


def _dedupe_consecutive_clicks(scenario: list[dict]) -> list[dict]:
    """연속한 두 click step 이 같은 page 의 같은 accessible name 을 가리키면
    하나만 남긴다. ``popup_to`` 가 있는 쪽을 우선 보존, 없으면 뒤쪽을 보존.

    step 번호는 압축 후 1..N 으로 재부여 (validator 가 연속 번호 요구).
    """
    if len(scenario) < 2:
        return scenario
    out: list[dict] = []
    for cur in scenario:
        if not out:
            out.append(cur)
            continue
        prev = out[-1]
        if (
            prev.get("page") == cur.get("page")
            and _normalized_click_identity(prev) is not None
            and _normalized_click_identity(prev) == _normalized_click_identity(cur)
        ):
            keep = prev if prev.get("popup_to") and not cur.get("popup_to") else cur
            log.info(
                "[Convert/AST] dedupe consecutive click step%s + step%s → keep step%s",
                prev.get("step"), cur.get("step"), keep.get("step"),
            )
            out[-1] = keep
            continue
        out.append(cur)
    # step 번호 재부여
    for i, s in enumerate(out, start=1):
        s["step"] = i
    return out


# IME composition / dead-key / modifier toggle 등 codegen 부산물.
# 모두 재생 시 의미 없거나 (CapsLock — 재생 시 IME 상태 다름)
# Playwright 가 거부 (Unidentified — Unknown key).
_IME_NOISE_KEYS = frozenset({"CapsLock", "Unidentified", "Process", "Compose", "Dead"})


def _strip_ime_noise(scenario: list[dict]) -> list[dict]:
    """한글 IME 사이트 녹화의 codegen 노이즈 step 정리.

    1. ``press`` value 가 IME 부산물 키이면 drop.
    2. 빈 ``fill`` 직후 같은 target/page 에 non-empty ``fill`` 이 오면 빈 fill drop
       (codegen 이 IME composition reset 으로 빈 fill 을 끼워 넣는 케이스).

    step 번호는 1..N 으로 재부여.
    """
    if not scenario:
        return scenario
    # 1단계 — press IME 노이즈 drop
    pruned: list[dict] = [
        s for s in scenario
        if not (s.get("action") == "press" and str(s.get("value", "")) in _IME_NOISE_KEYS)
    ]
    # 2단계 — 빈 fill 직후 같은 target 에 non-empty fill 이 있으면 빈 fill drop
    out: list[dict] = []
    for i, cur in enumerate(pruned):
        if (
            cur.get("action") == "fill"
            and str(cur.get("value", "")) == ""
            and i + 1 < len(pruned)
        ):
            nxt = pruned[i + 1]
            if (
                nxt.get("action") == "fill"
                and nxt.get("page") == cur.get("page")
                and nxt.get("target") == cur.get("target")
                and str(nxt.get("value", "")) != ""
            ):
                log.info(
                    "[Convert/AST] strip IME-noise empty fill step%s "
                    "(다음 step%s 에 non-empty fill 존재)",
                    cur.get("step"), nxt.get("step"),
                )
                continue
        out.append(cur)
    if len(out) != len(scenario):
        log.info(
            "[Convert/AST] IME-noise filter: %d → %d step",
            len(scenario), len(out),
        )
    for i, s in enumerate(out, start=1):
        s["step"] = i
    return out


# ─────────────────────────────────────────────────────────────────────────
# AST visitor
# ─────────────────────────────────────────────────────────────────────────


class _AstConverter(ast.NodeVisitor):
    """codegen .py 의 ``def run(playwright)`` body 를 순회하며 14-DSL 스텝 누적."""

    def __init__(self):
        self.steps: list[dict] = []
        # page-like 변수 — 시작 시 'page' 만 안전 (codegen 관례).
        # `with page.expect_popup() as pX_info:` + `pageX = pX_info.value` 발견
        # 시 동적으로 추가.
        self.page_vars: set[str] = {"page"}
        # popup info 변수 (``pX_info`` 형태) — `.value` 접근 시 page 로 승격
        self.popup_info_vars: set[str] = set()

    # def run(...) 만 처리 — 다른 함수는 noise 일 가능성 높음
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # `def run(playwright)` — codegen output.
        # `def test_*` — Zero-Touch QA regression export 가 만든 회귀 테스트.
        if node.name != "run" and not node.name.startswith("test_"):
            return
        for stmt in node.body:
            self._handle_stmt(stmt)

    def _handle_stmt(self, stmt: ast.stmt) -> None:
        """각 statement 를 처리. with / Expr / Assign / Assert / Try (body 재귀)."""
        if isinstance(stmt, ast.With):
            self._handle_with(stmt)
        elif isinstance(stmt, ast.Expr):
            self._handle_expr(stmt.value)
        elif isinstance(stmt, ast.Assign):
            self._handle_assign(stmt)
        elif isinstance(stmt, ast.Assert):
            self._handle_assert(stmt)
        elif isinstance(stmt, ast.Try):
            # tour 가 각 URL 블록을 try/except 로 감싸 첫 실패에서 abort 안 되게
            # 한다. 본 변환기는 핸들러는 무시하고 try.body 만 재귀 — 정상 흐름의
            # navigate / verify step 추출은 그대로 유지.
            for s in stmt.body:
                self._handle_stmt(s)
        # If/For 등은 codegen 이 만들지 않음 — 무시

    def _handle_assert(self, node: ast.Assert) -> None:
        """``assert ... `` 라인을 14-DSL ``verify`` step 으로 변환.

        지원 패턴:
          1. ``assert "X" not in page.url``           → url_not_contains
          2. ``assert "X" in page.url``               → url_contains
          3. ``assert len(page.inner_text("S")) >= N`` → min_text_length

        매칭 안 되는 assert 는 무시 — 변환되지 않은 assert 는 실 스크립트
        실행(`테스트코드 원본 실행`) 에서 그대로 raise 되므로 검증 자체는 보존됨.
        """
        step = (
            self._assert_to_url_membership(node.test)
            or self._assert_to_min_text_length(node.test)
        )
        if step is None:
            return
        step["step"] = len(self.steps) + 1
        step.setdefault("fallback_targets", [])
        self.steps.append(step)

    def _assert_to_url_membership(self, test: ast.expr) -> Optional[dict]:
        """``"X" in page.url`` / ``"X" not in page.url`` → verify step.

        반환된 dict 에 ``step`` 키는 caller 가 부여.
        """
        if not isinstance(test, ast.Compare):
            return None
        if len(test.ops) != 1 or len(test.comparators) != 1:
            return None
        op = test.ops[0]
        if not isinstance(op, (ast.In, ast.NotIn)):
            return None
        # 좌변: 문자열 상수
        if not (isinstance(test.left, ast.Constant) and isinstance(test.left.value, str)):
            return None
        needle = test.left.value
        # 우변: page.url (page 가 page_vars 안에 등록된 변수면 모두 허용).
        right = test.comparators[0]
        if not (
            isinstance(right, ast.Attribute)
            and right.attr == "url"
            and isinstance(right.value, ast.Name)
            and right.value.id in self.page_vars
        ):
            return None
        condition = "url_not_contains" if isinstance(op, ast.NotIn) else "url_contains"
        # target 은 ``body`` 로 고정 — executor 의 url_* 분기는 locator 를 쓰지
        # 않지만 main step flow 가 locator resolve 단계를 거쳐야 하므로 항상
        # 매칭 가능한 placeholder 가 필요. 'page.url' 같은 비-selector 를 두면
        # resolve 실패 → healing → FAIL 로 떨어짐 (회귀).
        return {
            "action": "verify",
            "target": "body",
            "value": needle,
            "condition": condition,
            "description": _describe_url_check(needle, isinstance(op, ast.NotIn)),
            "page": right.value.id,
        }

    def _assert_to_min_text_length(self, test: ast.expr) -> Optional[dict]:
        """``len(page.inner_text("S")) >= N`` → verify min_text_length step."""
        if not isinstance(test, ast.Compare):
            return None
        if len(test.ops) != 1 or len(test.comparators) != 1:
            return None
        op = test.ops[0]
        if not isinstance(op, (ast.GtE, ast.Gt)):
            return None
        # 좌변: len(page.inner_text("body")) 형태
        left = test.left
        if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == "len"):
            return None
        if len(left.args) != 1:
            return None
        inner = left.args[0]
        if not (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "inner_text"
            and isinstance(inner.func.value, ast.Name)
            and inner.func.value.id in self.page_vars
        ):
            return None
        # inner_text 의 첫 인자가 selector 문자열.
        if not inner.args or not (
            isinstance(inner.args[0], ast.Constant) and isinstance(inner.args[0].value, str)
        ):
            return None
        selector = inner.args[0].value
        # 우변: 정수 상수 (Gt 면 +1 보정 — 사용자 의도 보존).
        right = test.comparators[0]
        if not (isinstance(right, ast.Constant) and isinstance(right.value, int)):
            return None
        threshold = right.value + (1 if isinstance(op, ast.Gt) else 0)
        return {
            "action": "verify",
            "target": selector,
            "value": str(threshold),
            "condition": "min_text_length",
            "description": _describe_min_text_length(selector, threshold),
            "page": inner.func.value.id,
        }

    def _handle_with(self, node: ast.With) -> None:
        """``with page.expect_popup() as page1_info:`` 등 인식 + body 순회.

        body 의 마지막 stmt 가 popup 을 트리거하는 액션 (보통 click) 이라
        body 도 정상 순회한다.

        body 처리 후 trigger receiver 와 같은 ``page`` 값을 가진 마지막 click
        step 에 ``_pending_popup_info`` 임시 마커를 부착. 이후
        ``_handle_assign`` 에서 ``pageX = pX_info.value`` 발견 시 해당 마커를
        ``popup_to=pageX`` 로 resolve. (직렬화 직전 internal 키 cleanup.)
        """
        pending_info_var = None
        trigger_receiver = None
        for item in node.items:
            ctx = item.context_expr
            # page.expect_popup() 패턴 — receiver(page var) 도 함께 추출
            if (
                isinstance(ctx, ast.Call)
                and isinstance(ctx.func, ast.Attribute)
                and ctx.func.attr == "expect_popup"
                and isinstance(ctx.func.value, ast.Name)
                and item.optional_vars is not None
                and isinstance(item.optional_vars, ast.Name)
            ):
                self.popup_info_vars.add(item.optional_vars.id)
                pending_info_var = item.optional_vars.id
                trigger_receiver = ctx.func.value.id

        steps_before = len(self.steps)
        for stmt in node.body:
            self._handle_stmt(stmt)
        steps_after = len(self.steps)

        if pending_info_var is None or trigger_receiver is None:
            return
        # body 안에 누적된 step 들 중 trigger receiver 와 같은 page 의 마지막
        # click 을 popup 트리거로 본다 (codegen 관례 — popup-여는 액션은 click).
        for i in range(steps_after - 1, steps_before - 1, -1):
            s = self.steps[i]
            if s.get("page") == trigger_receiver and s.get("action") == "click":
                s["_pending_popup_info"] = pending_info_var
                return

    def _handle_assign(self, node: ast.Assign) -> None:
        """``page1 = page1_info.value`` 패턴 인식 → page1 을 page var 로 등록.

        ``_pending_popup_info`` 마커가 부착된 step 이 있으면 promoted page var
        를 ``popup_to`` 로 resolve (executor 가 이 alias 로 신규 page 를 등록).

        그 외 assign 은 무시. ``browser =`` / ``context =`` / ``page =`` 같은
        codegen 의 공통 prelude 도 본 변환기는 page 변수만 다루므로 영향 없음.
        """
        # 우변이 Attribute 이고 .value 접근 + Name 이 popup_info 면 페이지 승격
        v = node.value
        if (
            isinstance(v, ast.Attribute)
            and v.attr == "value"
            and isinstance(v.value, ast.Name)
            and v.value.id in self.popup_info_vars
        ):
            info_var = v.value.id
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    page_var = tgt.id
                    self.page_vars.add(page_var)
                    self._resolve_popup_trigger(info_var, page_var)

    def _resolve_popup_trigger(self, info_var: str, page_var: str) -> None:
        """``_pending_popup_info == info_var`` 마커를 ``popup_to=page_var`` 로 resolve."""
        for s in self.steps:
            if s.get("_pending_popup_info") == info_var:
                s["popup_to"] = page_var
                del s["_pending_popup_info"]
                return  # 1 popup-info ↔ 1 trigger

    def _handle_expr(self, expr: ast.expr) -> None:
        """statement 의 expression 본체를 액션으로 시도."""
        if not isinstance(expr, ast.Call):
            return
        step = self._convert_call_to_step(expr)
        if step is not None:
            # T-H 연계 — click 직전 hover 가 필요해 보이는 ancestor 가 target
            # chain 안에 정적으로 식별되면 hover step 을 prepend. DOM 접근 없이
            # selector 패턴만으로 추정 (보수적). false-positive 시 hover 가
            # no-op 으로 끝나 후속 click 에 부담 0.
            self._maybe_prepend_hover(step)
            step["step"] = len(self.steps) + 1
            step.setdefault("fallback_targets", [])
            self.steps.append(step)

    def _maybe_prepend_hover(self, step: dict) -> None:
        """click step 의 target chain 에서 hover trigger ancestor 추정 → hover step 삽입.

        조건:
          - action == 'click' 만 (다른 액션은 hover 가 의미 없음)
          - target 안에 ``>>`` chain 이 존재 (단일 segment 면 ancestor 정보 없음)
          - chain 의 leaf 가 아닌 segment 중 nav/menu/dropdown/gnb 등 신호 매칭

        hover trigger 가 발견되면 ``hover`` 액션 step 을 ``self.steps`` 에 직접 append
        (caller 가 click step 을 뒤이어 append).
        """
        if step.get("action") != "click":
            return
        target = str(step.get("target", ""))
        if " >> " not in target:
            return
        segments = [s.strip() for s in target.split(" >> ") if s.strip()]
        if len(segments) < 2:
            return
        # leaf 는 click 본체 — ancestor 후보는 그 외 segment.
        for i in range(len(segments) - 1):
            seg = segments[i]
            if not _seg_looks_like_hover_trigger(seg):
                continue
            # 해당 segment 까지의 chain 을 hover target 으로.
            hover_target = " >> ".join(segments[: i + 1])
            hover_step: dict = {
                "step": len(self.steps) + 1,
                "action": "hover",
                "target": hover_target,
                "value": "",
                "description": f"메뉴 펼치기 (heuristic, {seg})",
                "fallback_targets": [],
                "page": step.get("page", "page"),
            }
            self.steps.append(hover_step)
            return  # 여러 후보 중 가장 바깥 ancestor 1개만

    # ─────────────────────────────────────────────────────────────────────
    # Call → step 변환
    # ─────────────────────────────────────────────────────────────────────

    def _convert_call_to_step(self, call: ast.Call) -> Optional[dict]:
        """``page.X(...).Y(...)...`` 형태의 Call 을 단일 14-DSL 스텝으로 변환.

        매칭 안 되면 None — 호출 측이 무시한다.
        """
        # 1) expect(...).to_X(...) 패턴 (verify 액션)
        verify = self._try_parse_expect(call)
        if verify is not None:
            return verify

        # 2) 일반 메서드 chain — receiver chain + 마지막 메서드명 추출
        chain = self._collect_chain(call)
        if chain is None:
            return None
        receiver_root, segments, final_method, final_args, final_kwargs = chain

        if receiver_root not in self.page_vars and final_method != "goto":
            # page 변수가 아니면 무시 (browser/context/expect 등 prelude)
            return None

        # 3) page.goto(URL) — 단순 형태 (segments 비어 있고 final_method == goto)
        if final_method == "goto" and not segments and receiver_root in self.page_vars:
            url = self._literal_str(final_args[0]) if final_args else None
            if url is None:
                return None
            return {
                "action": "navigate", "target": "", "value": url,
                "description": f"{url}로 이동",
                "page": receiver_root,
            }

        # 4) page.wait_for_timeout(ms)
        if final_method == "wait_for_timeout" and not segments:
            ms = self._literal_int(final_args[0]) if final_args else None
            if ms is None:
                return None
            return {
                "action": "wait", "target": "", "value": str(ms),
                "description": f"{ms}ms 대기",
                "page": receiver_root,
            }

        # 5) page.route(PATTERN, lambda r: r.fulfill(...)) — mock_*
        if final_method == "route" and not segments:
            step = self._parse_mock_route(call)
            if step is not None:
                step["page"] = receiver_root
            return step

        # 6) target 이 필요한 액션 — segments 로부터 target 문자열 합성
        target = self._segments_to_target(segments)
        if target is None and final_method not in {"close"}:
            # target 추출 실패 — AST 가 다루지 못하는 패턴 → 호출자가 fallback
            return None

        step = self._dispatch_action(final_method, target or "", final_args, final_kwargs)
        if step is not None:
            step["page"] = receiver_root
        return step

    def _try_parse_expect(self, call: ast.Call) -> Optional[dict]:
        """``expect(<locator-expr>).to_have_text("X")`` / ``.to_be_visible()`` 변환."""
        if not isinstance(call.func, ast.Attribute):
            return None
        outer_method = call.func.attr
        if outer_method not in {"to_have_text", "to_be_visible"}:
            return None
        inner = call.func.value
        if not (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "expect"
        ):
            return None
        # inner.args[0] 은 locator 표현식 — chain 으로 처리
        if not inner.args:
            return None
        locator_expr = inner.args[0]
        chain = self._collect_chain(locator_expr)
        if chain is None:
            return None
        root, segments, _final_method_unused, _, _ = chain
        # locator 자체가 하나의 chain 끝이라, _collect_chain 은 마지막 호출을 final_*
        # 로 떼어낸다. 다시 합쳐 target 으로 전체 평탄화한다.
        full_segments = list(segments)
        full_segments.append(_LocatorSegment(_final_method_unused,
                                             _collect_args(locator_expr),
                                             _collect_kwargs(locator_expr)))
        if root not in self.page_vars:
            return None
        target = self._segments_to_target(full_segments)
        if target is None:
            return None
        if outer_method == "to_have_text":
            text = self._literal_str(call.args[0]) if call.args else ""
            return {
                "action": "verify", "target": target, "value": text,
                "description": f"텍스트 '{text}' 확인",
                "page": root,
            }
        # to_be_visible
        return {
            "action": "verify", "target": target, "value": "",
            "description": "요소 표시 확인",
            "page": root,
        }

    # ─────────────────────────────────────────────────────────────────────
    # 액션 dispatch
    # ─────────────────────────────────────────────────────────────────────

    def _dispatch_action(
        self, method: str, target: str,
        args: list[ast.expr], kwargs: list[ast.keyword],
    ) -> Optional[dict]:
        """final method 이름과 args 로부터 14-DSL 액션 step 생성."""
        if method == "click":
            return {
                "action": "click", "target": target, "value": "",
                "description": "클릭",
            }
        # fill / press_sequentially / type — 모두 *값 입력* 의미. 회귀 .py 가
        # 이전에 ``press_sequentially`` 로 emit 한 입력을 다시 import 했을 때
        # 시나리오에서 통째로 빠지던 회귀 방지 (2026-05-11 FLOW-USR-007 사례 —
        # 빈 fill 만 시나리오에 남고 typing 단계 소실). type 은 옛 alias.
        if method in ("fill", "press_sequentially", "type"):
            value = self._literal_str(args[0]) if args else ""
            return {
                "action": "fill", "target": target, "value": value,
                "description": f"'{value}' 입력",
            }
        if method == "press":
            value = self._literal_str(args[0]) if args else ""
            return {
                "action": "press", "target": target, "value": value,
                "description": f"{value} 키 입력",
            }
        if method == "select_option":
            # select_option("ko") 또는 select_option(label="...") / value="..."
            value = ""
            if args:
                value = self._literal_str(args[0]) or ""
            elif kwargs:
                # label= / value= 우선
                for kw in kwargs:
                    if kw.arg in ("label", "value"):
                        value = self._literal_str(kw.value) or ""
                        break
            return {
                "action": "select", "target": target, "value": value,
                "description": f"'{value}' 선택",
            }
        if method == "check":
            return {
                "action": "check", "target": target, "value": "on",
                "description": "체크",
            }
        if method == "uncheck":
            return {
                "action": "check", "target": target, "value": "off",
                "description": "체크 해제",
            }
        if method == "hover":
            return {
                "action": "hover", "target": target, "value": "",
                "description": "마우스 호버",
            }
        if method == "set_input_files":
            # set_input_files("path") 또는 ["path1", "path2"] (첫 항목)
            if not args:
                return None
            arg = args[0]
            path = None
            if isinstance(arg, ast.List) and arg.elts:
                path = self._literal_str(arg.elts[0])
            else:
                path = self._literal_str(arg)
            if path is None:
                return None
            return {
                "action": "upload", "target": target, "value": path,
                "description": f"'{path}' 파일 업로드",
            }
        if method == "drag_to":
            # args[0] = page.locator("dst") 같은 표현
            if not args:
                return None
            dst_chain = self._collect_chain(args[0])
            if dst_chain is None:
                return None
            _root, dst_segs, dst_final, _, _ = dst_chain
            full_dst = list(dst_segs) + [
                _LocatorSegment(dst_final, _collect_args(args[0]), _collect_kwargs(args[0]))
            ]
            dst_target = self._segments_to_target(full_dst)
            if dst_target is None:
                return None
            return {
                "action": "drag", "target": target, "value": dst_target,
                "description": "드래그 앤 드롭",
            }
        if method == "scroll_into_view_if_needed":
            return {
                "action": "scroll", "target": target, "value": "into_view",
                "description": "요소 위치로 스크롤",
            }
        if method == "close":
            # ``<page_var>.close()`` — 사용자가 녹화 중 명시적으로 닫은 탭/창.
            # ``page`` 필드는 caller 가 receiver_root (page var 이름) 로 채운다.
            return {
                "action": "close", "target": "", "value": "",
                "description": "창 닫기",
            }
        # go_back / 등 — 무시
        return None

    def _parse_mock_route(self, call: ast.Call) -> Optional[dict]:
        """``page.route(PATTERN, lambda r: r.fulfill(...))`` → mock_status / mock_data."""
        if len(call.args) < 2:
            return None
        pattern = self._literal_str(call.args[0])
        if pattern is None:
            return None
        handler = call.args[1]
        # handler 는 lambda — body 가 r.fulfill(status=N) 또는 r.fulfill(body=...)
        if not isinstance(handler, ast.Lambda):
            return None
        body = handler.body
        if not isinstance(body, ast.Call):
            return None
        if not (isinstance(body.func, ast.Attribute) and body.func.attr == "fulfill"):
            return None

        body_kw = None
        status_kw = None
        for kw in body.keywords:
            if kw.arg == "body":
                body_kw = kw.value
            elif kw.arg == "status":
                status_kw = kw.value
        if body_kw is not None:
            body_value = self._literal_str(body_kw)
            if body_value is None:
                return None
            return {
                "action": "mock_data", "target": pattern, "value": body_value,
                "description": f"{pattern} 응답 본문 모킹",
            }
        if status_kw is not None:
            status_value = self._literal_int(status_kw)
            if status_value is None:
                return None
            return {
                "action": "mock_status", "target": pattern,
                "value": str(status_value),
                "description": f"{pattern} 응답 상태 {status_value} 모킹",
            }
        return None

    # ─────────────────────────────────────────────────────────────────────
    # chain 분해 + target 합성
    # ─────────────────────────────────────────────────────────────────────

    def _collect_chain(self, node: ast.expr):
        """수신자 chain 을 모은다.

        ``page.X(...).Y(...).Z(...)`` 같은 형태에서:
          - root = "page" (Name)
          - segments = [X(...), Y(...)]  (마지막 Z 제외)
          - final_method = "Z"
          - final_args / final_kwargs = Z 의 인자

        ``.first`` 처럼 Attribute (Call 아님) 인 segment 도 포함한다.
        """
        if not isinstance(node, ast.Call):
            return None
        if not isinstance(node.func, ast.Attribute):
            return None

        final_method = node.func.attr
        final_args = node.args
        final_kwargs = node.keywords

        # 수신자 = node.func.value — 여기서부터 segments 수집
        segments = []
        cur = node.func.value
        while True:
            if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
                segments.append(_LocatorSegment(
                    method=cur.func.attr,
                    args=cur.args,
                    kwargs=cur.keywords,
                ))
                cur = cur.func.value
            elif isinstance(cur, ast.Attribute):
                # `.first` 같은 Attribute access (Call 아님)
                segments.append(_LocatorSegment(
                    method=cur.attr, args=[], kwargs=[],
                ))
                cur = cur.value
            else:
                break

        segments.reverse()

        if not isinstance(cur, ast.Name):
            return None
        return cur.id, segments, final_method, final_args, final_kwargs

    # get_by_X(literal) → "<prefix>=<literal>" 단일-인자 semantic helper 표.
    # role 은 name= kwarg 분기 때문에 별도 처리.
    _SEMANTIC_PREFIX_BY_METHOD = {
        "get_by_text": "text",
        "get_by_label": "label",
        "get_by_placeholder": "placeholder",
        "get_by_test_id": "testid",
        "get_by_title": "title",
        "get_by_alt_text": "alt",
    }

    def _segments_to_target(
        self, segments: list["_LocatorSegment"],
    ) -> Optional[str]:
        """chain 의 segment list 를 14-DSL ``target`` 문자열로 평탄화.

        규칙:
          - frame_locator(sel) → target 앞에 ``frame=<sel> >> `` 누적
          - get_by_role(role, name=N) → ``role=<role>, name=<N>``
          - get_by_text/label/placeholder/test_id/title/alt_text(t)
            → 각각 ``text=`` / ``label=`` / ``placeholder=`` / ``testid=`` /
              ``title=`` / ``alt=``
          - locator(sel) → ``sel`` (CSS/XPath 그대로). 이미 target 있으면 ``>> sel``
          - nth(N) → ``, nth=N`` 후미 부착
          - first → ``, nth=0``
          - filter(has_text=T) → ``, has_text=T``
        """
        target = ""
        frame_prefix_parts: list[str] = []
        # 임시 누적 변수 — locator chain 의 ``>>`` 결합용
        for seg in segments:
            method = seg.method

            if method == "frame_locator":
                if not seg.args:
                    return None
                sel = self._literal_str(seg.args[0])
                if sel is None:
                    return None
                frame_prefix_parts.append(f"frame={sel}")
                continue

            if method == "get_by_role":
                if not seg.args:
                    return None
                role = self._literal_str(seg.args[0])
                if role is None:
                    return None
                name = None
                exact = False
                for kw in seg.kwargs:
                    if kw.arg == "name":
                        name = self._literal_str(kw.value)
                    elif kw.arg == "exact":
                        # exact=True 보존 — substring 매칭으로 인해 의도와 다른
                        # element ("API" → "오픈API") 가 잡히는 케이스 방지.
                        # exact=False 는 default 라 굳이 emit 안 함.
                        if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            exact = True
                base = f"role={role}, name={name}" if name is not None else f"role={role}"
                if exact and name is not None:
                    base = f"{base}, exact=true"
                target = self._append_to_target(target, base)
                continue

            prefix = self._SEMANTIC_PREFIX_BY_METHOD.get(method)
            if prefix is not None:
                if not seg.args:
                    return None
                t = self._literal_str(seg.args[0])
                if t is None:
                    return None
                target = self._append_to_target(target, f"{prefix}={t}")
                continue

            if method == "locator":
                if not seg.args:
                    return None
                sel = self._literal_str(seg.args[0])
                if sel is None:
                    return None
                target = self._append_to_target(target, sel)
                continue

            if method == "nth":
                if not seg.args:
                    return None
                n = self._literal_int(seg.args[0])
                if n is None:
                    return None
                target = self._append_modifier(target, f"nth={n}")
                continue

            if method == "first":
                target = self._append_modifier(target, "nth=0")
                continue

            if method == "last":
                # codegen 이 ``.last`` 는 거의 안 만들지만 보존만.
                target = self._append_modifier(target, "nth=-1")
                continue

            if method == "filter":
                # filter(has_text="...") 만 표준 처리
                for kw in seg.kwargs:
                    if kw.arg == "has_text":
                        v = self._literal_str(kw.value)
                        if v is None:
                            return None
                        target = self._append_modifier(target, f"has_text={v}")
                        break

            # 그 외 unknown — 무시 (line fallback 으로 가지 않게 부분 처리)
            # 예: .all() / .count() 같은 read-only 는 액션 본체에 없으므로 도달 불가

        if frame_prefix_parts:
            joined_frame = " >> ".join(frame_prefix_parts)
            target = f"{joined_frame} >> {target}" if target else joined_frame
        return target if target else None

    @staticmethod
    def _append_to_target(existing: str, segment: str) -> str:
        """기존 target 에 새 selector segment 를 ``>>`` 로 결합."""
        if not existing:
            return segment
        return f"{existing} >> {segment}"

    @staticmethod
    def _append_modifier(existing: str, modifier: str) -> str:
        """``, modifier`` 를 후미에 붙인다 (nth/has_text 같은 옵션)."""
        if not existing:
            # base selector 없이 modifier 만 있는 비정상 케이스 — 그대로 둔다
            return modifier
        return f"{existing}, {modifier}"

    # ─────────────────────────────────────────────────────────────────────
    # 리터럴 추출 헬퍼
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _literal_str(node: ast.expr) -> Optional[str]:
        """ast.Constant(str) 만 추출. f-string / 변수 / 표현식 결합은 None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    @staticmethod
    def _literal_int(node: ast.expr) -> Optional[int]:
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool):
            return node.value
        # 문자열로 저장된 숫자도 허용 (codegen 은 거의 안 만들지만)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return int(node.value)
            except ValueError:
                return None
        return None


# ─────────────────────────────────────────────────────────────────────────
# Locator segment 표현 — 내부 자료구조
# ─────────────────────────────────────────────────────────────────────────


class _LocatorSegment:
    """chain 의 한 마디. method=호출명, args/kwargs=ast 노드 그대로."""

    __slots__ = ("method", "args", "kwargs")

    def __init__(self, method: str, args, kwargs):
        self.method = method
        self.args = list(args) if args else []
        self.kwargs = list(kwargs) if kwargs else []


# T-H 연계 — chain segment 가 hover-trigger 처럼 보이는지 정적 식별 (DOM 무관).
# 보수적: 명시적 신호만 매칭 → false-positive 최소화.
import re as _re

# nav 태그, GNB/LNB/navbar 클래스명, 드롭다운/메뉴 단어, ARIA 의 hover 신호 속성을
# segment 문자열에서 보수적으로 식별. false-positive 시 hover 가 no-op 으로 끝나
# click 부담은 0.
_HOVER_TRIGGER_PATTERNS = [
    _re.compile(r"\bnav\b"),
    _re.compile(r"\b(?:gnb|lnb|navbar|nav-)\b", _re.I),
    _re.compile(r"\b(?:dropdown|drop-down)\b", _re.I),
    _re.compile(r"\b(?:menu|submenu|menubar)\b", _re.I),
    _re.compile(r"aria-haspopup"),
    _re.compile(r"aria-expanded"),
    _re.compile(r"role=(?:menu|menubar|listbox|combobox)\b"),
]


def _seg_looks_like_hover_trigger(seg: str) -> bool:
    """segment 가 hover trigger 가능성이 있는 ancestor 인지 보수적 추정."""
    s = seg.strip()
    if not s:
        return False
    for pat in _HOVER_TRIGGER_PATTERNS:
        if pat.search(s):
            return True
    return False


def _collect_args(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.Call):
        return list(node.args)
    return []


def _collect_kwargs(node: ast.expr) -> list[ast.keyword]:
    if isinstance(node, ast.Call):
        return list(node.keywords)
    return []
