"""visibility healer — Locator 가 hidden 일 때 5단계로 visible 화 시도.

executor (시나리오 실행 시), codegen_trace_wrapper (Replay UI raw .py 실행 시),
regression_generator (회귀 .py 본문에 인라인 emit 시) 세 곳이 같은 로직을
사용한다. 외부 의존성 0 — Playwright sync API + page 안 JS 만 쓴다.

설계 메모:
- 모든 함수는 ``self`` 를 받지 않는 모듈 함수. wrapper monkey-patch 와 회귀
  .py 인라인 emit 양쪽에서 같은 코드를 사용할 수 있도록 instance state 의존
  완전 제거.
- 회귀 .py 인라인 emit 용 소스 텍스트는 ``INLINE_SOURCE`` 상수로 노출.
  regression_generator 가 그대로 .py 본문에 붙여 자기완결성 확보.
"""

from __future__ import annotations

import logging
from typing import Optional

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)


# 페이지 안에서 실행되는 JS — element 의 ancestor 중 hoverable trigger 후보를
# 추출. cascade hover (outermost → innermost) 의 입력.
VISIBILITY_HEALER_JS = r"""
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

  function hoverTriggerSelectors(rule) {
    const out = [];
    if (!rule.selectorText || !rule.selectorText.includes(':hover')) return out;
    for (const part of rule.selectorText.split(',').map(s => s.trim())) {
      if (!part.includes(':hover')) continue;
      const idx = part.indexOf(':hover');
      let trigger = part.slice(0, idx);
      trigger = trigger.replace(/[\s>+~]+$/, '').trim();
      if (trigger) out.push(trigger);
    }
    return out;
  }
  function isHoverTrigger(node) {
    try {
      for (const sheet of document.styleSheets) {
        let rules;
        try { rules = sheet.cssRules; } catch (_) { continue; }
        for (const r of rules || []) {
          for (const sel of hoverTriggerSelectors(r)) {
            try { if (node.matches(sel)) return true; } catch (_) {}
          }
        }
      }
    } catch (_) {}
    return false;
  }

  const out = [];
  let cur = el;
  let depth = 0;
  while (cur && cur !== document.body && depth < 12) {
    let reason = null;
    if (cur.getAttribute && cur.getAttribute('aria-haspopup')) reason = 'aria-haspopup';
    else if (cur.getAttribute && cur.getAttribute('aria-expanded') === 'false') reason = 'aria-expanded=false';
    else {
      const role = cur.getAttribute && cur.getAttribute('role');
      if (role && ['menu','menubar','listbox','tooltip','combobox'].includes(role)) reason = 'role=' + role;
    }
    if (!reason) {
      const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
      if (['nav','details','summary'].includes(tag)) reason = 'tag=' + tag;
    }
    if (!reason && cur.getAttribute) {
      const ds = cur.getAttribute('data-state');
      if (ds === 'closed') reason = 'data-state=closed';
    }
    if (!reason && isHoverTrigger(cur)) reason = ':hover-css';

    if (reason) {
      out.push({ path: cssPath(cur), reason });
    }
    cur = cur.parentElement;
    depth++;
  }
  return out;
}
"""


def find_visible_sibling(locator: Locator, step_id=None) -> Optional[Locator]:
    """Locator 가 다중 매치이고 ``.first`` 가 hidden 일 때 visible 한 형제 swap.

    Playwright 1.36+ 의 ``filter(visible=True)`` 사용.
    """
    try:
        visible = locator.filter(visible=True)
        if visible.count() > 0:
            first = visible.first
            if first.is_visible():
                log.info(
                    "[Step %s] visibility-healer — 형제 매치 swap (filter(visible=True).first)",
                    step_id,
                )
                return first
    except Exception:  # noqa: BLE001
        pass
    return None


def heal_visibility(
    page: Page, locator: Locator, step_id=None,
    *, pre_actions_out: Optional[list] = None,
) -> Optional[Locator]:
    """element 가 hidden 이면 5단계로 visible 화 시도.

    순서 (각 단계는 visible 되면 즉시 단축):
      (1) ``scroll_into_view_if_needed``
      (2) cascade ancestor hover (outermost → innermost) — VISIBILITY_HEALER_JS
          가 추출한 hoverable 후보 순차 hover
      (3) page-level activator hover — header/nav/main/body
      (4) size-aware poll — 최대 2s
      (5) sibling swap — filter(visible=True).first

    Args:
        pre_actions_out: caller 가 빈 list 를 넘기면 *통과시킨* cascade hover /
            scroll / page-hover 시퀀스가 ``{"action":..., "target":...}`` dict 로
            append 된다. regression_generator 가 회귀 .py 본 스텝 *앞에* 그대로
            emit 해 같은 환경에서 동일하게 통과시킨다 (안전망을 매번 다시 돌릴
            필요 없음).

    Returns:
        visible 한 다른 형제 매치를 찾았으면 그 Locator. 그 외 None
        (locator 자체를 그대로 사용해도 OK 임을 의미).
    """
    def _record(action: str, target: str) -> None:
        if pre_actions_out is not None:
            pre_actions_out.append({"action": action, "target": target})

    try:
        if locator.is_visible():
            return None
    except Exception:
        return None  # invalid locator — caller 가 후속 처리

    # (1) scroll_into_view — 통과시키면 회귀 시퀀스에 scroll 1단계 기록.
    try:
        locator.scroll_into_view_if_needed(timeout=1500)
        page.wait_for_timeout(150)
        if locator.is_visible():
            log.info("[Step %s] visibility-healer 복구 — scroll_into_view", step_id)
            # scroll 대상은 *원본 locator 자체* — selector 표현이 회귀 .py 의 본
            # 스텝과 같으므로 별도 기록 불필요. (회귀 .py 가 click 직전에 같은
            # locator 로 scroll 을 한 줄 emit 해도 무해하지만, 본 스텝의 click
            # 이 Playwright 의 actionability check 안에서 자동 scroll 하므로
            # 일반적으로 0 effect — 기록 안 함.)
            return None
    except Exception:
        pass

    # (2) cascade ancestor hover — 통과시키는 *전체 hover 체인* 을 기록.
    try:
        candidates = locator.evaluate(VISIBILITY_HEALER_JS)
    except Exception as e:  # noqa: BLE001
        log.debug("[Step %s] visibility-healer evaluate 실패: %s", step_id, e)
        candidates = []

    chain = list(reversed(candidates))[:5]  # 최대 5단계
    accumulated: list[str] = []
    hovered_path: list[str] = []
    for cand in chain:
        sel = cand.get("path") or ""
        reason = cand.get("reason") or "unknown"
        if not sel:
            continue
        try:
            ancestor = page.locator(sel).first
            ancestor.hover(timeout=1500)
            page.wait_for_timeout(150)
            accumulated.append(sel)
            hovered_path.append(f"{sel}({reason})")
            if locator.is_visible():
                log.info(
                    "[Step %s] visibility-healer 복구 — cascade hover %s",
                    step_id, " > ".join(hovered_path),
                )
                # 통과시킨 hover 시퀀스 *전체* 를 기록 — outermost 부터 차례로.
                for s in accumulated:
                    _record("hover", s)
                return None
        except Exception:  # noqa: BLE001
            continue

    # (3) page-level activator probe — 통과시킨 activator 1개만 기록.
    for activator_sel in ("header", "nav", "main", "body"):
        try:
            target = page.locator(activator_sel).first
            if target.count() == 0:
                continue
            target.hover(timeout=1000)
            page.wait_for_timeout(200)
            if locator.is_visible():
                log.info(
                    "[Step %s] visibility-healer 복구 — page-level hover (%s)",
                    step_id, activator_sel,
                )
                _record("hover", activator_sel)
                return None
        except Exception:  # noqa: BLE001
            continue

    # (4) size-aware poll — 단순 대기. 회귀 .py 가 같은 사이트라면 같은 transition
    # 이 작동할 가능성이 높으므로 wait 한 줄 기록.
    try:
        for _ in range(10):  # 200ms x 10 = 2s
            page.wait_for_timeout(200)
            if locator.is_visible():
                log.info("[Step %s] visibility-healer 복구 — size poll", step_id)
                _record("wait", "2000")
                return None
    except Exception:  # noqa: BLE001
        pass

    # (5) sibling swap — 다른 element 로 교체. selector 역추출 불가하므로
    # caller (executor) 가 StepResult.target 에 별도 표현 사용 (fix #1 의 경로).
    # pre_actions 에는 기록 안 함.
    sibling = find_visible_sibling(locator, step_id)
    if sibling is not None:
        return sibling

    log.debug(
        "[Step %s] visibility-healer — 모든 전략 무력 (scroll/ancestor/page-hover/size-poll/sibling)",
        step_id,
    )
    return None
