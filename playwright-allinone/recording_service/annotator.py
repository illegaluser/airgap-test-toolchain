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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic annotate — 실 페이지 visibility probe + ancestor hover 자동 식별.
#
# 정적 ``annotate_script`` 의 사각지대 해소: chain 이 단일 segment
# (``page.get_by_role("button", name="사용신청 관리")``) 라 ancestor 가 selector
# 문자열에 안 잡힐 때, 실 DOM 의 hidden 상태 + ``aria-haspopup`` /
# ``[role=menu]`` / ``:hover-css`` ancestor 를 동적으로 탐색해 hover 라인 prepend.
#
# 알고리즘:
#   1. AST 에서 ``page.<chain>`` 액션 시퀀스 추출 (navigate / click / fill /
#      press / check / select_option). converter_ast 의 _AstConverter 인스턴스
#      재활용 → 14-DSL target 으로 변환 후 LocatorResolver 로 Playwright
#      Locator 재구성.
#   2. sandbox Playwright 세션으로 sequential replay. click 직전:
#       a. element 가 attached & visible 인지 짧은 timeout 으로 검사.
#       b. invisible 이면 executor 의 _VISIBILITY_HEALER_JS 실행 → ancestor
#          후보 list (``{path, reason}``) 획득.
#       c. 각 후보 cssPath 에 hover 시도 → target 이 visible 되는지 확인 →
#          첫 성공의 path 를 trigger 로 기록.
#   3. 기록된 (lineno, css_path) 들을 src .py 의 click 라인 직전에 prepend
#      해서 dst .py 로 작성.
#
# 실패 시 (browser 미동작 / navigate 실패 / element 추출 실패): 정적
# annotate_script 로 graceful fallback. dynamic 의 가치는 *추가* 정확도지
# *대체* 가 아님.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _ReplayAction:
    """AST 에서 추출한 단일 액션 — sandbox replay 의 단위.

    ``kind`` 는 14-DSL action 명. ``target`` 은 LocatorResolver 가 받을 14-DSL
    target 문자열 (navigate 면 빈 문자열). ``value`` 는 fill/press 등의 인자.
    ``lineno`` 는 src .py 의 1-based 라인 번호 — click 의 hover prepend 위치.
    """

    kind: str
    lineno: int
    target: str = ""
    value: str = ""


@dataclass
class _HoverTrigger:
    css_path: str
    reason: str


def _extract_replay_actions(tree: ast.Module) -> list[_ReplayAction]:
    """AST 의 ``def run`` body 에서 replay 가능한 액션을 순서대로 추출.

    converter_ast 의 _AstConverter 를 재사용 — 같은 14-DSL 변환 인프라
    (popup chain 인식 / page_vars 추적 / chain 평탄화) 를 거쳐 결과 step 의
    ``action`` / ``target`` / ``value`` 를 그대로 _ReplayAction 으로 매핑.
    각 step 의 lineno 는 원본 ast.Expr 의 lineno.

    popup with-body 안 액션은 _AstConverter 가 popup_info_vars / page_vars 로
    인식해 step["page"] 를 page1 등으로 마킹하지만, 본 dynamic annotator 는
    main page 의 hover trigger 만 다루므로 step["page"] != "page" 인 액션은
    skip (popup 안 dropdown 의 hover 추정은 별도 PLAN).
    """
    from zero_touch_qa.converter_ast import _AstConverter

    conv = _AstConverter()
    actions: list[_ReplayAction] = []

    # ast.Expr 만 대상으로 — assert / with / assign 은 step 빌드 안 됨.
    # _AstConverter 의 visit 흐름을 그대로 따라가기 위해 def run body 만 walk.
    run_func = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run"),
        None,
    )
    if run_func is None:
        return actions

    for node in ast.walk(run_func):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        # converter 가 popup_info_vars 를 채우려면 _handle_with 도 거쳐야 하므로
        # 안전하게 visit 수행 — 단 본 함수는 step 누적된 후 lineno 매핑이 안 되므로
        # 직접 _convert_call_to_step 호출 + page_vars 인식만 활용.
        step = conv._convert_call_to_step(node.value)
        if step is None:
            continue
        # main page 가 아닌 액션 (popup 안) 은 본 PR 범위 외.
        if step.get("page", "page") != "page":
            continue
        action = step.get("action", "")
        if action not in {"navigate", "click", "fill", "press", "check", "uncheck", "select"}:
            continue
        actions.append(_ReplayAction(
            kind=action, lineno=node.lineno,
            target=str(step.get("target", "")),
            value=str(step.get("value", "")),
        ))
    return actions


def _attached_and_hidden(locator, timeout_ms: int) -> bool:
    """element 가 attached 됐고 hidden 인지 검사. attached 실패 / 이미 visible
    이면 False (hover 가 무의미한 케이스). True 면 hover ancestor 탐색 가치 있음.
    """
    try:
        locator.first.wait_for(state="attached", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return False
    try:
        return not locator.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


# 보조 후보 추출 — hidden ancestor 의 첫 visible 부모를 trigger 후보로 추가.
# executor 의 _VISIBILITY_HEALER_JS 가 ``aria-haspopup`` / ``role=menu`` /
# ``:hover-css`` 같은 명시적 trigger 만 잡는데, 실 사이트는 표준 selector
# (``nav#gnb > li:hover``) 와 다른 임의 selector 로 :hover rule 을 작성하는
# 경우가 많아 매칭이 누락됨 (예: portal.koreaconnect.kr).
#
# 본 휴리스틱은 selector 매칭 없이 layout 만 봄: target 의 ancestor chain 에서
# ``display:none`` / ``visibility:hidden`` 인 element 가 있으면, 그 직후의
# 첫 visible ancestor 가 hover 시 그 chain 을 펼치는 trigger 일 가능성 높음.
_HIDDEN_PARENT_TRIGGER_JS = r"""
el => {
  function cssPath(node) {
    if (!node || node === document.body) return 'body';
    if (node.id) return '#' + CSS.escape(node.id);
    let parts = [];
    let cur = node;
    while (cur && cur !== document.body && parts.length < 6) {
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      const tag = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (!parent) { parts.unshift(tag); break; }
      const same = [...parent.children].filter(c => c.tagName === cur.tagName);
      const idx = same.indexOf(cur) + 1;
      parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${idx})` : tag);
      cur = parent;
    }
    return parts.join(' > ');
  }
  const out = [];
  let cur = el;
  let depth = 0;
  let hit_hidden = false;
  while (cur && cur !== document.body && depth < 12) {
    const cs = getComputedStyle(cur);
    const hidden = cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0';
    if (hidden) {
      hit_hidden = true;
    } else if (hit_hidden) {
      // 첫 visible ancestor — hover trigger 후보. 더 외부 visible 도 후보지만
      // 가장 안쪽이 좁은 효과의 hover 라 false-positive 부담 ↓.
      out.push({ path: cssPath(cur), reason: 'visible-ancestor-of-hidden' });
      // 그 위 visible ancestor 도 fallback 으로 추가 (1 단계만).
      const parent = cur.parentElement;
      if (parent && parent !== document.body) {
        out.push({ path: cssPath(parent), reason: 'visible-ancestor-of-hidden+1' });
      }
      break;
    }
    cur = cur.parentElement;
    depth++;
  }
  return out;
}
"""


def _hover_candidates_for(locator) -> list[dict]:
    """element 의 ancestor 후보 list 반환.

    1차: executor 의 ``_VISIBILITY_HEALER_JS`` (명시적 aria/role/CSS 트리거).
    2차 (1차 결과로 hover 못 찾을 때 fallback): ``_HIDDEN_PARENT_TRIGGER_JS``
    (layout 기반 첫 visible ancestor). 두 결과를 그대로 합쳐 호출자가 순서대로
    시도. 실패 / 빈 결과 → 빈 list.
    """
    from zero_touch_qa.executor import _VISIBILITY_HEALER_JS

    try:
        handle = locator.first.element_handle()
    except Exception:  # noqa: BLE001
        return []
    if handle is None:
        return []
    raw: list[dict] = []
    try:
        cands = handle.evaluate(_VISIBILITY_HEALER_JS)
        if isinstance(cands, list):
            raw.extend(cands)
    except Exception:  # noqa: BLE001
        pass
    try:
        extra = handle.evaluate(_HIDDEN_PARENT_TRIGGER_JS)
        if isinstance(extra, list):
            raw.extend(extra)
    except Exception:  # noqa: BLE001
        pass
    # path dedup — 같은 element 가 1차 (aria/role/CSS) 와 2차 (layout) 양쪽에
    # 잡힐 수 있다. 1차 reason 이 더 명시적이라 먼저 추가된 항목 유지.
    out: list[dict] = []
    seen: set[str] = set()
    for c in raw:
        path = c.get("path") if isinstance(c, dict) else None
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(c)
    return out


def _find_hover_trigger_dynamic(
    page, locator,
    visibility_timeout_ms: int,
    hover_settle_ms: int,
) -> Optional[_HoverTrigger]:
    """element 가 hidden 일 때 ancestor 들 중 hover 후 target 을 visible 시키는
    첫 ancestor 의 stable cssPath + reason 반환.

    visible / attached 안 됨 / 후보 없음 / 어떤 hover 도 효과 없음 → None.

    후보 순서: 가장 외부 ancestor 부터 (executor JS 의 emit 은 안쪽 → 외부 라
    여기서 reverse). 외부 ancestor 의 hover 가 일반적으로 dropdown 전체를
    트리거하므로 더 안정적. 안쪽 ancestor 들도 dropdown 안 hidden 인 경우가
    많아 자기 자신을 hover 못 함 (force=True 로 강제하지 않으면 실패).

    hover 는 ``force=True`` 로 강제 — actionability check 가 hidden ancestor 를
    거부하지 않게. element 가 attached 됐으면 mouseover 이벤트는 발사됨.
    """
    if not _attached_and_hidden(locator, visibility_timeout_ms):
        return None
    cands = _hover_candidates_for(locator)
    for cand in reversed(cands):  # 외부 ancestor 부터 시도
        css_path = cand.get("path")
        reason = cand.get("reason", "")
        if not css_path or css_path == "body":
            continue
        try:
            page.locator(css_path).first.hover(
                timeout=visibility_timeout_ms, force=True,
            )
            page.wait_for_timeout(hover_settle_ms)
            if locator.first.is_visible():
                return _HoverTrigger(css_path=css_path, reason=reason)
        except Exception:  # noqa: BLE001
            continue
    return None


def _replay_one(page, action: _ReplayAction, *, nav_timeout_ms: int, action_timeout_ms: int) -> None:
    """단일 액션 replay — 실패는 swallow (annotate 가 시나리오 진행을 끊지 않음)."""
    from zero_touch_qa.locator_resolver import LocatorResolver

    if action.kind == "navigate":
        if action.value:
            try:
                page.goto(action.value, timeout=nav_timeout_ms)
            except Exception as e:  # noqa: BLE001
                log.warning("[annotate dynamic] navigate 실패 (%s): %s", action.value, e)
        return
    resolver = LocatorResolver(page)
    loc = resolver.resolve(action.target)
    if loc is None:
        return
    try:
        if action.kind == "click":
            loc.click(timeout=action_timeout_ms)
        elif action.kind == "fill":
            loc.fill(action.value, timeout=action_timeout_ms)
        elif action.kind == "press":
            loc.press(action.value, timeout=action_timeout_ms)
        elif action.kind == "check":
            loc.check(timeout=action_timeout_ms)
        elif action.kind == "uncheck":
            loc.uncheck(timeout=action_timeout_ms)
        elif action.kind == "select":
            loc.select_option(action.value, timeout=action_timeout_ms)
    except Exception:  # noqa: BLE001
        # replay 단계의 액션 실패는 무시 — annotate 목적은 click 의 hover trigger
        # 식별이지 시나리오 검증이 아님.
        pass


def annotate_script_dynamic(
    src_path: str,
    dst_path: str,
    *,
    storage_state_in: Optional[str] = None,
    headless: bool = True,
    nav_timeout_ms: int = 15_000,
    action_timeout_ms: int = 5_000,
    visibility_timeout_ms: int = 2_000,
    hover_settle_ms: int = 500,
) -> AnnotateResult:
    """src .py 를 sandbox replay 하면서 hidden click 의 ancestor hover trigger 를
    실 DOM 에서 식별, dst .py 에 hover 라인을 prepend 한다.

    실패 시 정적 ``annotate_script`` 로 fallback (회귀 0).

    Args:
        src_path: 원본 codegen .py
        dst_path: hover 가 prepend 된 산출물 경로
        storage_state_in: 인증 storage_state JSON 경로 (선택). 비공개 페이지의
            메뉴 / dropdown 을 보려면 필요.
        headless: 브라우저 표시 여부 (default True)
        nav_timeout_ms: page.goto 타임아웃
        action_timeout_ms: click/fill/press 등 일반 액션 타임아웃
        visibility_timeout_ms: hidden element 의 ancestor hover 후 visible
            대기 타임아웃 (짧게)
        hover_settle_ms: hover 후 dropdown 애니메이션 settle 대기

    Returns:
        ``AnnotateResult`` — triggers 에는 ``"L<lineno>: <css_path> (<reason>)"``
        형식의 디버그 문자열 누적.
    """
    p = Path(src_path)
    if not p.is_file():
        raise FileNotFoundError(f"annotate src 없음: {src_path}")
    source = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise RuntimeError(f"AST 파싱 실패: {e}") from e

    actions = _extract_replay_actions(tree)
    if not actions:
        # navigate / click 둘 다 없으면 그대로 복사.
        Path(dst_path).write_text(source, encoding="utf-8")
        log.info("[annotate dynamic] %s → %s — 액션 없음 (그대로 복사)", src_path, dst_path)
        return AnnotateResult(src_path, dst_path, 0, 0, [])

    try:
        triggers_by_lineno, examined, triggers_log = _run_dynamic_pass(
            actions=actions,
            storage_state_in=storage_state_in,
            headless=headless,
            nav_timeout_ms=nav_timeout_ms,
            action_timeout_ms=action_timeout_ms,
            visibility_timeout_ms=visibility_timeout_ms,
            hover_settle_ms=hover_settle_ms,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[annotate dynamic] sandbox 실패 → static fallback: %s", e)
        return annotate_script(src_path, dst_path)

    if not triggers_by_lineno:
        # dynamic 으로 못 찾았으면 정적 패턴이라도 시도 (회귀 0).
        return annotate_script(src_path, dst_path)

    _write_annotated_with_triggers(source, triggers_by_lineno, dst_path)
    log.info(
        "[annotate dynamic] %s → %s — examined=%d injected=%d",
        src_path, dst_path, examined, len(triggers_by_lineno),
    )
    return AnnotateResult(
        src_path=src_path, dst_path=dst_path,
        injected=len(triggers_by_lineno),
        examined_clicks=examined,
        triggers=triggers_log,
    )


def _run_dynamic_pass(
    *,
    actions: list[_ReplayAction],
    storage_state_in: Optional[str],
    headless: bool,
    nav_timeout_ms: int,
    action_timeout_ms: int,
    visibility_timeout_ms: int,
    hover_settle_ms: int,
) -> tuple[dict[int, _HoverTrigger], int, list[str]]:
    """sandbox Playwright 세션에서 actions 를 sequential replay → click 직전
    visibility probe → trigger 식별. (triggers_by_lineno, examined, log) 반환.
    """
    from playwright.sync_api import sync_playwright

    triggers_by_lineno: dict[int, _HoverTrigger] = {}
    examined = 0
    triggers_log: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context_kwargs: dict = {}
        if storage_state_in and Path(storage_state_in).is_file():
            context_kwargs["storage_state"] = storage_state_in
        ctx = browser.new_context(**context_kwargs)
        page = ctx.new_page()
        for action in actions:
            if action.kind == "click":
                examined += 1
                trig = _probe_click_trigger(
                    page, action,
                    visibility_timeout_ms=visibility_timeout_ms,
                    hover_settle_ms=hover_settle_ms,
                )
                if trig is not None:
                    triggers_by_lineno[action.lineno] = trig
                    triggers_log.append(
                        f"L{action.lineno}: {trig.css_path} ({trig.reason})"
                    )
            _replay_one(
                page, action,
                nav_timeout_ms=nav_timeout_ms,
                action_timeout_ms=action_timeout_ms,
            )
        ctx.close()
        browser.close()
    return triggers_by_lineno, examined, triggers_log


def _resolve_for_probe(page, target: str):
    """probe 전용 hidden-aware locator.

    ``LocatorResolver.resolve`` 는 a11y tree 기반 ``get_by_role`` 을 쓰는데
    Playwright 가 ``visibility:hidden`` element 는 a11y tree 에서 제외 →
    dropdown 안 hidden 버튼을 못 찾음. dynamic annotate 는 정확히 그
    케이스를 다루므로 ``include_hidden=True`` 로 강제 검색.
    """
    from zero_touch_qa.locator_resolver import (
        _ROLE_NAME_RE, _ROLE_PREFIX, _split_name_exact, LocatorResolver,
    )

    if target.startswith(_ROLE_PREFIX):
        m = _ROLE_NAME_RE.match(target)
        if m:
            name, exact = _split_name_exact(m.group(2))
            return page.get_by_role(
                m.group(1).strip(), name=name, exact=exact, include_hidden=True,
            )
        # name= 없는 광범위 role 은 hover trigger 추정 의미 없음 → skip.
        return None
    # role= 외 — LocatorResolver 의 일반 흐름 (text/label/placeholder/css/xpath).
    return LocatorResolver(page).resolve(target)


def _probe_click_trigger(
    page, action: _ReplayAction,
    *, visibility_timeout_ms: int, hover_settle_ms: int,
) -> Optional[_HoverTrigger]:
    """click action 의 target 을 resolve 후 hidden 이면 ancestor hover trigger 식별."""
    loc = _resolve_for_probe(page, action.target)
    if loc is None:
        return None
    return _find_hover_trigger_dynamic(
        page, loc,
        visibility_timeout_ms=visibility_timeout_ms,
        hover_settle_ms=hover_settle_ms,
    )


def _write_annotated_with_triggers(
    source: str, triggers_by_lineno: dict[int, _HoverTrigger], dst_path: str,
) -> None:
    """src source 의 click 라인 직전에 hover line 을 prepend 후 dst 에 write."""
    lines = source.splitlines(keepends=True)
    for lineno in sorted(triggers_by_lineno.keys(), reverse=True):
        css_path = triggers_by_lineno[lineno].css_path
        idx = lineno - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        indent = line[: len(line) - len(line.lstrip())]
        hover_line = (
            f"{indent}page.locator({css_path!r}).first.hover()  "
            f"# auto-annotated (dynamic) for hidden-click healing\n"
        )
        lines.insert(idx, hover_line)
    Path(dst_path).write_text("".join(lines), encoding="utf-8")
