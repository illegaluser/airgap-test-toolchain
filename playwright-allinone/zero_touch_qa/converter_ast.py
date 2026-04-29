"""AST-based Playwright codegen → 14-DSL converter (T-A / P0.4 core).

Design: docs/PLAN_PRODUCTION_READINESS.md §"T-A — AST-based converter"

Removes the limits of the existing line-based regex in [converter.py](converter.py):
- Lost actions on popup tab variables (page1, page2, …)
- Information loss on ``.nth(N)`` / ``.first`` / ``.filter(has_text=...)``
- Cannot flatten ``page.locator(...).locator(...)`` nested chains
- Loses the ``page.frame_locator(...).get_by_role(...)`` chain

This module parses with ``ast.parse`` and walks the ``def run(playwright)`` body to:
- Track page-like variable scopes (including popup chains)
- Flatten the receiver chain of action calls — including ``.nth/.first/.filter/locator/frame_locator``
  — into a 14-DSL ``target`` string
- Handle all 14 actions of the DSL

On non-standard patterns (lambdas, variable aliases, dynamic dispatch), raise
``CodegenAstError`` — the caller graceful-degrades to the line-based fallback.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


class CodegenAstError(RuntimeError):
    """Explicit error for the AST-conversion stage. Triggers the caller's line fallback."""


def convert_via_ast(file_path: str, output_dir: str) -> list[dict]:
    """Parse the codegen .py file via AST and convert it into a 14-DSL scenario.

    Args:
        file_path: absolute path to the codegen-output .py file
        output_dir: directory to save scenario.json

    Returns:
        List of 14-DSL steps. Each step has step/action/target/value/description/fallback_targets.

    Raises:
        FileNotFoundError: input file missing
        CodegenAstError: AST parse/convert failure (signal to trigger the line fallback)
    """
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(f"file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise CodegenAstError(f"AST parse failed: {e}") from e

    converter = _AstConverter()
    converter.visit(tree)

    scenario = converter.steps
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "scenario.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)

    log.info(
        "[Convert/AST] %s -> %s (%d steps converted)",
        file_path, output_path, len(scenario),
    )
    return scenario


# ─────────────────────────────────────────────────────────────────────────
# AST visitor
# ─────────────────────────────────────────────────────────────────────────


class _AstConverter(ast.NodeVisitor):
    """Walk the ``def run(playwright)`` body of a codegen .py and accumulate 14-DSL steps."""

    def __init__(self):
        self.steps: list[dict] = []
        # page-like variables — only 'page' is safe at start (codegen convention).
        # We add dynamically when we see `with page.expect_popup() as pX_info:` +
        # `pageX = pX_info.value`.
        self.page_vars: set[str] = {"page"}
        # popup info variables (``pX_info`` form) — promoted to page on `.value` access
        self.popup_info_vars: set[str] = set()

    # Only process def run(...) — other functions are likely noise
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name != "run":
            return
        for stmt in node.body:
            self._handle_stmt(stmt)

    def _handle_stmt(self, stmt: ast.stmt) -> None:
        """Process each statement. Only with / Expr / Assign matter."""
        if isinstance(stmt, ast.With):
            self._handle_with(stmt)
        elif isinstance(stmt, ast.Expr):
            self._handle_expr(stmt.value)
        elif isinstance(stmt, ast.Assign):
            self._handle_assign(stmt)
        # codegen does not produce If/For/Try, etc. — ignore

    def _handle_with(self, node: ast.With) -> None:
        """Recognize ``with page.expect_popup() as page1_info:`` etc. and walk the body.

        The last stmt of the body is usually the action (typically click) that
        triggers the popup, so we still walk the body normally.
        """
        for item in node.items:
            ctx = item.context_expr
            # page.expect_popup() pattern
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
        """Recognize the ``page1 = page1_info.value`` pattern → register page1 as a page var.

        Other assigns are ignored. codegen's common prelude
        (``browser =`` / ``context =`` / ``page =``) is unaffected because this
        converter only tracks page variables.
        """
        # If the RHS is an Attribute access to .value on a Name in popup_info, promote it
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
        """Try to interpret the statement's expression body as an action."""
        if not isinstance(expr, ast.Call):
            return
        step = self._convert_call_to_step(expr)
        if step is not None:
            # T-H integration — when an ancestor that likely needs a hover before the
            # click is statically identifiable in the target chain, prepend a hover
            # step. Inferred from selector patterns alone, without DOM access
            # (conservative). On false positives the hover ends as a no-op, adding
            # zero overhead to the following click.
            self._maybe_prepend_hover(step)
            step["step"] = len(self.steps) + 1
            step.setdefault("fallback_targets", [])
            self.steps.append(step)

    def _maybe_prepend_hover(self, step: dict) -> None:
        """Infer a hover-trigger ancestor in the click step's target chain → insert a hover step.

        Conditions:
          - action == 'click' only (hover is meaningless for other actions)
          - target contains a ``>>`` chain (a single segment has no ancestor info)
          - One of the non-leaf segments matches a signal like nav / menu / dropdown / gnb

        When a hover trigger is found, append a ``hover`` action step directly
        to ``self.steps`` (the caller appends the click step right after).
        """
        if step.get("action") != "click":
            return
        target = str(step.get("target", ""))
        if " >> " not in target:
            return
        segments = [s.strip() for s in target.split(" >> ") if s.strip()]
        if len(segments) < 2:
            return
        # The leaf is the click target — ancestor candidates are all other segments.
        for i in range(len(segments) - 1):
            seg = segments[i]
            if not _SEG_LOOKS_LIKE_HOVER_TRIGGER(seg):
                continue
            # Use the chain up to that segment as the hover target.
            hover_target = " >> ".join(segments[: i + 1])
            hover_step: dict = {
                "step": len(self.steps) + 1,
                "action": "hover",
                "target": hover_target,
                "value": "",
                "description": f"open menu (heuristic, {seg})",
                "fallback_targets": [],
            }
            self.steps.append(hover_step)
            return  # only the outermost ancestor among multiple candidates

    # ─────────────────────────────────────────────────────────────────────
    # Call → step conversion
    # ─────────────────────────────────────────────────────────────────────

    def _convert_call_to_step(self, call: ast.Call) -> Optional[dict]:
        """Convert a ``page.X(...).Y(...)...`` Call into a single 14-DSL step.

        Returns None if no match — caller ignores it.
        """
        # 1) expect(...).to_X(...) pattern (verify action)
        verify = self._try_parse_expect(call)
        if verify is not None:
            return verify

        # 2) Generic method chain — extract receiver chain + final method name
        chain = self._collect_chain(call)
        if chain is None:
            return None
        receiver_root, segments, final_method, final_args, final_kwargs = chain

        if receiver_root not in self.page_vars and final_method != "goto":
            # Ignore non-page variables (browser/context/expect prelude, etc.)
            return None

        # 3) page.goto(URL) — simple form (no segments and final_method == goto)
        if final_method == "goto" and not segments and receiver_root in self.page_vars:
            url = self._literal_str(final_args[0]) if final_args else None
            if url is None:
                return None
            return {
                "action": "navigate", "target": "", "value": url,
                "description": f"navigate to {url}",
            }

        # 4) page.wait_for_timeout(ms)
        if final_method == "wait_for_timeout" and not segments:
            ms = self._literal_int(final_args[0]) if final_args else None
            if ms is None:
                return None
            return {
                "action": "wait", "target": "", "value": str(ms),
                "description": f"wait {ms}ms",
            }

        # 5) page.route(PATTERN, lambda r: r.fulfill(...)) — mock_*
        if final_method == "route" and not segments:
            return self._parse_mock_route(call)

        # 6) Action that needs a target — synthesize target string from segments
        target = self._segments_to_target(segments)
        if target is None and final_method not in {"close"}:
            # Target extraction failed — pattern AST cannot handle → caller falls back
            return None

        return self._dispatch_action(final_method, target or "", final_args, final_kwargs)

    def _try_parse_expect(self, call: ast.Call) -> Optional[dict]:
        """Convert ``expect(<locator-expr>).to_have_text("X")`` / ``.to_be_visible()``."""
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
        # inner.args[0] is the locator expression — handle as chain
        if not inner.args:
            return None
        locator_expr = inner.args[0]
        chain = self._collect_chain(locator_expr)
        if chain is None:
            return None
        root, segments, _final_method_unused, _, _ = chain
        # The locator itself is a chain end, so _collect_chain peels off the last
        # call as final_*. Re-attach to flatten the whole thing into a target.
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
                "description": f"verify text '{text}'",
            }
        # to_be_visible
        return {
            "action": "verify", "target": target, "value": "",
            "description": "verify visible",
        }

    # ─────────────────────────────────────────────────────────────────────
    # Action dispatch
    # ─────────────────────────────────────────────────────────────────────

    def _dispatch_action(
        self, method: str, target: str,
        args: list[ast.expr], kwargs: list[ast.keyword],
    ) -> Optional[dict]:
        """Build a 14-DSL action step from the final method name and args."""
        if method == "click":
            return {
                "action": "click", "target": target, "value": "",
                "description": "click",
            }
        if method == "fill":
            value = self._literal_str(args[0]) if args else ""
            return {
                "action": "fill", "target": target, "value": value,
                "description": f"fill '{value}'",
            }
        if method == "press":
            value = self._literal_str(args[0]) if args else ""
            return {
                "action": "press", "target": target, "value": value,
                "description": f"press {value}",
            }
        if method == "select_option":
            # select_option("ko") or select_option(label="...") / value="..."
            value = ""
            if args:
                value = self._literal_str(args[0]) or ""
            elif kwargs:
                # Prefer label= / value=
                for kw in kwargs:
                    if kw.arg in ("label", "value"):
                        value = self._literal_str(kw.value) or ""
                        break
            return {
                "action": "select", "target": target, "value": value,
                "description": f"select '{value}'",
            }
        if method == "check":
            return {
                "action": "check", "target": target, "value": "on",
                "description": "check",
            }
        if method == "uncheck":
            return {
                "action": "check", "target": target, "value": "off",
                "description": "uncheck",
            }
        if method == "hover":
            return {
                "action": "hover", "target": target, "value": "",
                "description": "hover",
            }
        if method == "set_input_files":
            # set_input_files("path") or ["path1", "path2"] (first item)
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
                "description": f"upload '{path}'",
            }
        if method == "drag_to":
            # args[0] = expression like page.locator("dst")
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
                "description": "drag and drop",
            }
        if method == "scroll_into_view_if_needed":
            return {
                "action": "scroll", "target": target, "value": "into_view",
                "description": "scroll into view",
            }
        # close / go_back / etc. — ignore
        return None

    def _parse_mock_route(self, call: ast.Call) -> Optional[dict]:
        """``page.route(PATTERN, lambda r: r.fulfill(...))`` → mock_status / mock_data."""
        if len(call.args) < 2:
            return None
        pattern = self._literal_str(call.args[0])
        if pattern is None:
            return None
        handler = call.args[1]
        # The handler is a lambda — body is r.fulfill(status=N) or r.fulfill(body=...)
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
                "description": f"mock response body for {pattern}",
            }
        if status_kw is not None:
            status_value = self._literal_int(status_kw)
            if status_value is None:
                return None
            return {
                "action": "mock_status", "target": pattern,
                "value": str(status_value),
                "description": f"mock response status {status_value} for {pattern}",
            }
        return None

    # ─────────────────────────────────────────────────────────────────────
    # Chain decomposition + target synthesis
    # ─────────────────────────────────────────────────────────────────────

    def _collect_chain(self, node: ast.expr):
        """Collect the receiver chain.

        For ``page.X(...).Y(...).Z(...)``:
          - root = "page" (Name)
          - segments = [X(...), Y(...)]  (excluding the final Z)
          - final_method = "Z"
          - final_args / final_kwargs = Z's args

        Also includes Attribute (non-Call) segments such as ``.first``.
        """
        if not isinstance(node, ast.Call):
            return None
        if not isinstance(node.func, ast.Attribute):
            return None

        final_method = node.func.attr
        final_args = node.args
        final_kwargs = node.keywords

        # Receiver = node.func.value — start collecting segments here
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
                # Attribute access like `.first` (not a Call)
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
        """Flatten a list of chain segments into a 14-DSL ``target`` string.

        Rules:
          - frame_locator(sel) → accumulate ``frame=<sel> >> `` in front of target
          - get_by_role(role, name=N) → ``role=<role>, name=<N>``
          - get_by_text(t) / get_by_label(t) / get_by_placeholder(t) / get_by_test_id(t)
            → ``text=t`` / ``label=t`` / ``placeholder=t`` / ``testid=t`` respectively
          - locator(sel) → ``sel`` (CSS/XPath verbatim). If target is non-empty, ``>> sel``
          - nth(N) → append ``, nth=N``
          - first → ``, nth=0``
          - filter(has_text=T) → ``, has_text=T``
        """
        target = ""
        frame_prefix_parts: list[str] = []
        # Working buffer for ``>>``-joining the locator chain
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
                # codegen rarely produces ``.last`` but we keep it.
                target = self._append_modifier(target, "nth=-1")
                continue

            if method == "filter":
                # Only standardize filter(has_text="...")
                for kw in seg.kwargs:
                    if kw.arg == "has_text":
                        v = self._literal_str(kw.value)
                        if v is None:
                            return None
                        target = self._append_modifier(target, f"has_text={v}")
                        break
                continue

            # Other unknowns — ignore (partial handling so we don't drop to line fallback).
            # e.g. read-only methods like .all() / .count() never appear as action bodies.

        if frame_prefix_parts:
            joined_frame = " >> ".join(frame_prefix_parts)
            target = f"{joined_frame} >> {target}" if target else joined_frame
        return target if target else None

    @staticmethod
    def _append_to_target(existing: str, segment: str) -> str:
        """Join a new selector segment onto the existing target with ``>>``."""
        if not existing:
            return segment
        return f"{existing} >> {segment}"

    @staticmethod
    def _append_modifier(existing: str, modifier: str) -> str:
        """Append ``, modifier`` (options like nth/has_text)."""
        if not existing:
            # Unusual case: modifier only, no base selector — keep as is
            return modifier
        return f"{existing}, {modifier}"

    # ─────────────────────────────────────────────────────────────────────
    # Literal-extraction helpers
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _literal_str(node: ast.expr) -> Optional[str]:
        """Extract only ast.Constant(str). Returns None for f-strings / variables / expressions."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    @staticmethod
    def _literal_int(node: ast.expr) -> Optional[int]:
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool):
            return node.value
        # Allow integers stored as strings (codegen rarely does this)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return int(node.value)
            except ValueError:
                return None
        return None


# ─────────────────────────────────────────────────────────────────────────
# Locator segment representation — internal data structure
# ─────────────────────────────────────────────────────────────────────────


class _LocatorSegment:
    """One link of a chain. method=call name, args/kwargs=raw ast nodes."""

    __slots__ = ("method", "args", "kwargs")

    def __init__(self, method: str, args, kwargs):
        self.method = method
        self.args = list(args) if args else []
        self.kwargs = list(kwargs) if kwargs else []


# T-H integration — statically identify whether a chain segment looks like a
# hover trigger (DOM-agnostic). Conservative: match only explicit signals to
# minimize false positives.
import re as _re

_HOVER_TRIGGER_PATTERNS = [
    _re.compile(r"\bnav\b"),                       # tag=nav
    _re.compile(r"\b(?:gnb|lnb|navbar|nav-)\b", _re.I),
    _re.compile(r"\b(?:dropdown|drop-down)\b", _re.I),
    _re.compile(r"\b(?:menu|submenu|menubar)\b", _re.I),
    _re.compile(r"aria-haspopup"),
    _re.compile(r"aria-expanded"),
    _re.compile(r"role=(?:menu|menubar|listbox|combobox)\b"),
]


def _SEG_LOOKS_LIKE_HOVER_TRIGGER(seg: str) -> bool:
    """Conservatively infer whether a segment is an ancestor that could be a hover trigger."""
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
