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
from typing import Optional

log = logging.getLogger(__name__)


class CodegenAstError(RuntimeError):
    """AST 변환 단계의 명시적 에러. 호출 측 line fallback 트리거."""


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
        if node.name != "run":
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
        return {
            "action": "verify",
            "target": "page.url",
            "value": needle,
            "condition": condition,
            "description": f"URL {'미포함' if isinstance(op, ast.NotIn) else '포함'} 검증 — '{needle}'",
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
            "description": f"본문 텍스트 길이 ≥ {threshold} 검증",
        }

    def _handle_with(self, node: ast.With) -> None:
        """``with page.expect_popup() as page1_info:`` 등 인식 + body 순회.

        body 의 마지막 stmt 가 popup 을 트리거하는 액션 (보통 click) 이라
        body 도 정상 순회한다.
        """
        for item in node.items:
            ctx = item.context_expr
            # page.expect_popup() 패턴
            if (
                isinstance(ctx, ast.Call)
                and isinstance(ctx.func, ast.Attribute)
                and ctx.func.attr == "expect_popup"
                and item.optional_vars is not None
                and isinstance(item.optional_vars, ast.Name)
            ):
                self.popup_info_vars.add(item.optional_vars.id)
        for stmt in node.body:
            self._handle_stmt(stmt)

    def _handle_assign(self, node: ast.Assign) -> None:
        """``page1 = page1_info.value`` 패턴 인식 → page1 을 page var 로 등록.

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
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.page_vars.add(tgt.id)

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
            }

        # 4) page.wait_for_timeout(ms)
        if final_method == "wait_for_timeout" and not segments:
            ms = self._literal_int(final_args[0]) if final_args else None
            if ms is None:
                return None
            return {
                "action": "wait", "target": "", "value": str(ms),
                "description": f"{ms}ms 대기",
            }

        # 5) page.route(PATTERN, lambda r: r.fulfill(...)) — mock_*
        if final_method == "route" and not segments:
            return self._parse_mock_route(call)

        # 6) target 이 필요한 액션 — segments 로부터 target 문자열 합성
        target = self._segments_to_target(segments)
        if target is None and final_method not in {"close"}:
            # target 추출 실패 — AST 가 다루지 못하는 패턴 → 호출자가 fallback
            return None

        return self._dispatch_action(final_method, target or "", final_args, final_kwargs)

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
            }
        # to_be_visible
        return {
            "action": "verify", "target": target, "value": "",
            "description": "요소 표시 확인",
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
        if method == "fill":
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
        # close / go_back / 등 — 무시
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

    def _segments_to_target(
        self, segments: list["_LocatorSegment"],
    ) -> Optional[str]:
        """chain 의 segment list 를 14-DSL ``target`` 문자열로 평탄화.

        규칙:
          - frame_locator(sel) → target 앞에 ``frame=<sel> >> `` 누적
          - get_by_role(role, name=N) → ``role=<role>, name=<N>``
          - get_by_text(t) / get_by_label(t) / get_by_placeholder(t) / get_by_test_id(t)
            → 각각 ``text=t`` / ``label=t`` / ``placeholder=t`` / ``testid=t``
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
                for kw in seg.kwargs:
                    if kw.arg == "name":
                        name = self._literal_str(kw.value)
                        break
                base = f"role={role}, name={name}" if name is not None else f"role={role}"
                target = self._append_to_target(target, base)
                continue

            if method == "get_by_text":
                if not seg.args:
                    return None
                t = self._literal_str(seg.args[0])
                if t is None:
                    return None
                target = self._append_to_target(target, f"text={t}")
                continue

            if method == "get_by_label":
                if not seg.args:
                    return None
                t = self._literal_str(seg.args[0])
                if t is None:
                    return None
                target = self._append_to_target(target, f"label={t}")
                continue

            if method == "get_by_placeholder":
                if not seg.args:
                    return None
                t = self._literal_str(seg.args[0])
                if t is None:
                    return None
                target = self._append_to_target(target, f"placeholder={t}")
                continue

            if method == "get_by_test_id":
                if not seg.args:
                    return None
                t = self._literal_str(seg.args[0])
                if t is None:
                    return None
                target = self._append_to_target(target, f"testid={t}")
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
