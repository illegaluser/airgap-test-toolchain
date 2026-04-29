"""TR.7+ — Auto-inject hover into codegen-original ``.py`` (static annotate).

Applies the same rule as the (4) converter heuristic
(`_SEG_LOOKS_LIKE_HOVER_TRIGGER`) to the codegen original source itself.
Motivation — codegen Output Replay runs the original on the host without
conversion, so it gets neither (1) executor healer nor (4) converter protection.
This module scans for ``page.<chain>.click()`` lines and, when the chain
contains a hover-trigger ancestor, prepends a ``.hover()`` call on the
matching chain prefix immediately before the click.

Design limits (static analysis):
  - DOM-agnostic — inferred only from the selector strings in chain segments.
  - On false-positive: the hover ends as a no-op, so the click cost is 0.
  - On false-negative: original codegen behavior preserved — no regression.

The dynamic variant (annotate after a real-page visibility probe) will be
hooked into ``run_replay`` later in a separate module.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

# Reuse the same pattern as (4) — import the function rather than relocating the definition.
from zero_touch_qa.converter_ast import _SEG_LOOKS_LIKE_HOVER_TRIGGER

log = logging.getLogger(__name__)


@dataclass
class AnnotateResult:
    src_path: str
    dst_path: str
    injected: int                 # number of hover lines added
    examined_clicks: int          # total click calls examined
    triggers: list[str]           # list of detected trigger source segments (for debugging)


def annotate_script(src_path: str, dst_path: str) -> AnnotateResult:
    """Read ``src_path``, insert hover lines before clicks that look like they need them, and write to ``dst_path``."""
    p = Path(src_path)
    if not p.is_file():
        raise FileNotFoundError(f"annotate src missing: {src_path}")

    source = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise RuntimeError(f"AST parse failed: {e}") from e

    # Accumulate as line_no(1-based) → list[hover_source_line].
    insertions: dict[int, list[str]] = {}
    triggers: list[str] = []
    examined = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "click"):
            continue
        examined += 1

        chain_root = call.func.value  # the chain part of `<chain>.click()`
        trigger_node = _find_hover_trigger_in_chain(chain_root)
        if trigger_node is None:
            continue
        segment = ast.get_source_segment(source, trigger_node)
        if not segment:
            continue
        # Extract the leading indent of the click line.
        line_idx = node.lineno - 1
        line = source.splitlines()[line_idx] if line_idx < len(source.splitlines()) else ""
        indent = line[: len(line) - len(line.lstrip())]
        hover_line = f"{indent}{segment}.hover()  # auto-annotated for hidden-click healing\n"
        insertions.setdefault(node.lineno, []).append(hover_line)
        triggers.append(segment)

    # Insert into source in reverse line-number order (so earlier indices don't shift).
    if insertions:
        lines = source.splitlines(keepends=True)
        for lineno in sorted(insertions.keys(), reverse=True):
            for hover_line in reversed(insertions[lineno]):
                lines.insert(lineno - 1, hover_line)
        new_source = "".join(lines)
    else:
        new_source = source

    Path(dst_path).write_text(new_source, encoding="utf-8")
    log.info(
        "[annotate] %s → %s — examined=%d injected=%d",
        src_path, dst_path, examined, sum(len(v) for v in insertions.values()),
    )
    return AnnotateResult(
        src_path=src_path,
        dst_path=dst_path,
        injected=sum(len(v) for v in insertions.values()),
        examined_clicks=examined,
        triggers=triggers,
    )


def _find_hover_trigger_in_chain(node: ast.expr) -> ast.expr | None:
    """Walk back from the chain root and return the outermost segment that could be a hover trigger.

    Chain example: ``page.locator('nav#gnb').locator('li').get_by_role('link', name='X')``.
    Each sub-Call's selector argument is checked against the trigger heuristic —
    the prefix up to the trigger closest to the root becomes the hover target.
    """
    # Flatten the chain — innermost Call first, walking toward the root.
    candidates: list[ast.expr] = []
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        candidates.append(cur)
        cur = cur.func.value
    # candidates[0] = leaf (just before click), candidates[-1] = closest to the root.
    # Pick the trigger closest to the root (hovering a broader ancestor is safer).
    for c in reversed(candidates):
        if not isinstance(c, ast.Call) or not isinstance(c.func, ast.Attribute):
            continue
        method = c.func.attr
        if method not in ("locator", "filter", "get_by_role", "frame_locator"):
            # get_by_text / get_by_label etc. are typically used as leaves, so we
            # exclude them as hover trigger candidates — too broad a risk.
            continue
        # Apply the trigger heuristic to the argument text.
        arg_text = _stringify_args(c)
        if _SEG_LOOKS_LIKE_HOVER_TRIGGER(arg_text):
            return c
    return None


def _stringify_args(call: ast.Call) -> str:
    parts: list[str] = []
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            parts.append(a.value)
    for kw in call.keywords:
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            parts.append(f"{kw.arg}={kw.value.value}")
        elif kw.arg:
            parts.append(kw.arg)
    return " ".join(parts)
