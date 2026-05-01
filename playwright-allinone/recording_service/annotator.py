"""TR.7+ — Codegen 원본 ``.py`` 의 hover 자동 주입 (static annotate).

(4) converter heuristic 과 동일 규칙(`_seg_looks_like_hover_trigger`) 을 codegen
원본 소스 자체에 적용한다. 동기 — codegen Output Replay 는 변환 없이 원본을
그대로 호스트에서 돌리는 경로라 (1) executor healer / (4) converter 의 보호를
받지 못한다. 이 모듈은 ``page.<chain>.click()`` 라인을 찾아 그 chain 안에
hover-trigger ancestor 가 있으면 같은 chain 의 prefix 에 ``.hover()`` 를 호출
하는 라인을 click 직전에 prepend 한다.

설계 한계 (정적 분석):
  - DOM 무관 — chain segment 의 selector 문자열만 보고 추정.
  - false-positive 시: hover 가 no-op 으로 끝나 click 부담 0.
  - false-negative 시: 기존 codegen 동작 그대로 — 회귀 0.

dynamic 변형 (실 페이지 visibility probe 후 annotate) 은 향후 ``run_replay``
훅에 결합하여 별도 모듈에서 처리.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

# (4) 와 동일 패턴 재사용 — 정의 위치를 옮기지 않고 함수만 import.
from zero_touch_qa.converter_ast import _seg_looks_like_hover_trigger

log = logging.getLogger(__name__)


@dataclass
class AnnotateResult:
    src_path: str
    dst_path: str
    injected: int                 # 추가된 hover 라인 수
    examined_clicks: int          # 검사한 click 호출 총수
    triggers: list[str]           # 감지된 trigger source segment list (디버그용)


def annotate_script(src_path: str, dst_path: str) -> AnnotateResult:
    """``src_path`` 를 읽고 hover 가 필요해 보이는 click 앞에 hover 라인을 삽입해 ``dst_path`` 에 쓴다."""
    p = Path(src_path)
    if not p.is_file():
        raise FileNotFoundError(f"annotate src 없음: {src_path}")

    source = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise RuntimeError(f"AST 파싱 실패: {e}") from e

    # line_no(1-based) → list[hover_source_line] 으로 누적.
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

        chain_root = call.func.value  # `<chain>.click()` 의 chain 부분
        trigger_node = _find_hover_trigger_in_chain(chain_root)
        if trigger_node is None:
            continue
        segment = ast.get_source_segment(source, trigger_node)
        if not segment:
            continue
        # click 라인의 leading 인덴트 추출.
        line_idx = node.lineno - 1
        line = source.splitlines()[line_idx] if line_idx < len(source.splitlines()) else ""
        indent = line[: len(line) - len(line.lstrip())]
        hover_line = f"{indent}{segment}.hover()  # auto-annotated for hidden-click healing\n"
        insertions.setdefault(node.lineno, []).append(hover_line)
        triggers.append(segment)

    # 라인 번호 역순으로 source 에 삽입 (앞 라인 인덱스가 안 밀리도록).
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
    """chain root 부터 거슬러 올라가며 hover-trigger 가능성이 있는 가장 바깥 segment 반환.

    chain 예: ``page.locator('nav#gnb').locator('li').get_by_role('link', name='X')``.
    각 sub-Call 의 selector 인자를 보고 trigger 휴리스틱 매칭 — 가장 root 에 가까운
    trigger 까지의 prefix 를 hover 대상으로.
    """
    # chain 평탄화 — 가장 inner Call 부터 root 방향으로.
    candidates: list[ast.expr] = []
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        candidates.append(cur)
        cur = cur.func.value
    # candidates[0] = leaf (click 직전), candidates[-1] = root 에 가장 가까움.
    # 가장 root 에 가까운 trigger 를 선택 (hover 가 광범위 ancestor 일수록 안전).
    for c in reversed(candidates):
        if not isinstance(c, ast.Call) or not isinstance(c.func, ast.Attribute):
            continue
        method = c.func.attr
        if method not in ("locator", "filter", "get_by_role", "frame_locator"):
            # get_by_text / get_by_label 등은 leaf 로 자주 쓰이므로 hover trigger
            # 후보에서 제외 — 너무 광범위해질 위험.
            continue
        # 인자 텍스트로 trigger 휴리스틱 매칭.
        arg_text = _stringify_args(c)
        if _seg_looks_like_hover_trigger(arg_text):
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
