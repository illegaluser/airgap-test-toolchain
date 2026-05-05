import re
import difflib
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)


class LocalHealer:
    """
    LLM 호출 없이(비용 0) 현재 페이지의 DOM을 스캔하여
    실패한 타겟과 가장 유사한 요소를 찾아 반환한다.

    액션별 검색 대상:
      - fill/press:     input, textarea, [role='textbox'], [role='searchbox'], [contenteditable]
      - select:         select, [role='listbox'], [role='combobox'], option, [role='option']
      - hover:          button, a, [role='menuitem'], [role='tab'], nav a, [aria-haspopup]
      - click/check 등: button, a, [role='button'], [role='link'], [role='menuitem'], [role='tab']
    """

    SELECTOR_MAP = {
        "fill": (
            "input, textarea, [role='textbox'], [role='searchbox'], "
            "[contenteditable='true']"
        ),
        "press": (
            "input, textarea, [role='textbox'], [role='searchbox'], "
            "[contenteditable='true']"
        ),
        "select": (
            "select, [role='listbox'], [role='combobox'], "
            "option, [role='option']"
        ),
        "hover": (
            "button, a, [role='button'], [role='link'], "
            "[role='menuitem'], [role='tab'], [role='menu'], "
            "nav a, [aria-haspopup], [role='tooltip']"
        ),
    }

    DEFAULT_SELECTOR = (
        "button, a, [role='button'], [role='link'], "
        "[role='menuitem'], [role='tab']"
    )

    def __init__(self, page: Page, threshold: float = 0.8):
        self.page = page
        self.threshold = threshold

    def try_heal(self, step: dict) -> Locator | None:
        """step의 target과 유사한 요소를 DOM에서 검색한다.

        T-C (P0.2) — target 이 ``frame=<sel> >> ...`` chain 으로 시작하면
        같은 FrameLocator 안에서만 fallback 을 시도해 frame 경계를 넘지 않는다.
        그렇지 않으면 기존대로 page 전체 스캔.
        """
        action = step["action"].lower()
        target = step.get("target", "")

        selector = self.SELECTOR_MAP.get(action, self.DEFAULT_SELECTOR)
        clean_target = self._clean_target(target)
        if len(clean_target) <= 1:
            return None

        scope, scope_label = self._frame_scope_for_target(target)

        best_match = None
        highest_ratio = 0.0

        try:
            candidates = scope.locator(selector).all()
        except Exception:
            return None

        for el in candidates:
            # disabled element 는 매칭 후보에서 제외 — 같은 disabled 노드를
            # 100% 유사도로 재선택해 같은 timeout 을 또 발생시키는 패턴 차단.
            if self._is_disabled(el):
                continue
            text = self._extract_text(el)
            if not text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_target, text).ratio()
            if ratio > self.threshold and ratio > highest_ratio:
                highest_ratio = ratio
                best_match = el

        if best_match:
            log.info(
                "  [로컬복구 성공] 유사도 %.0f%% 매칭 (scope=%s)",
                highest_ratio * 100, scope_label,
            )
        return best_match

    def _frame_scope_for_target(self, target):
        """target 이 ``frame=<sel>`` 로 시작하면 해당 FrameLocator 반환, 아니면 page.

        반환값 ``(scope, label)`` 의 ``scope`` 는 ``.locator(...)`` 가 가능한 객체
        (Page 또는 FrameLocator). label 은 로그용.
        """
        if not isinstance(target, str):
            return self.page, "page"
        # 합성 chain 의 첫 segment 만 검사. 중첩 frame 은 두 번째 segment 도
        # frame= 일 수 있으므로 누적.
        chain = [s.strip() for s in target.split(" >> ") if s.strip()]
        cur = self.page
        consumed = 0
        for seg in chain:
            if seg.startswith("frame="):
                sel = seg[len("frame="):].strip()
                if not sel:
                    break
                try:
                    cur = cur.frame_locator(sel)
                except Exception:
                    return self.page, "page"
                consumed += 1
                continue
            break
        if consumed == 0:
            return self.page, "page"
        return cur, f"frame[{consumed}]"

    @staticmethod
    def _clean_target(target) -> str:
        """시맨틱 접두사를 제거하여 순수 텍스트를 추출한다.

        T-C (P0.2) — ``frame=...>>`` chain 일 때는 마지막 segment 만 사용한다.
        healer 가 frame-scoped fallback 을 수행할 때 텍스트 매칭은 leaf
        descriptor 기준이어야 의미 있다.

        ``role=X, name=Y`` 패턴은 사람이 읽을 수 있는 ``Y`` 만 추출 (X 는
        accessibility role 이라 텍스트 매칭에 의미 없음). 모디파이어
        (``, nth=N`` / ``, has_text=T``) 는 검사 전에 떨어낸다 — converter
        AST 가 emit 하는 실제 형태에 그대로 작동하도록 하기 위함.
        """
        s = str(target)
        if " >> " in s:
            s = s.split(" >> ")[-1]
        # 후미 modifier (nth / has_text) — 텍스트 매칭에 noise.
        s = re.sub(r",\s*(nth|has_text)=.*$", "", s).strip()
        # `role=X, name=Y` → Y. accessibility role 자체는 텍스트 노이즈.
        m = re.match(r"role=.+?,\s*name=(.+)$", s)
        if m:
            return m.group(1).strip()
        # name 없는 단독 role= 은 텍스트 매칭 불가 — 빈 문자열 반환 (try_heal 에서 skip).
        if s.startswith("role="):
            return ""
        # text/label/placeholder/testid/frame/shadow prefix 단독은 안전하게 strip.
        s = re.sub(r"^(text|label|placeholder|testid|frame|shadow)=", "", s)
        return s.strip()

    @staticmethod
    def _extract_text(el) -> str:
        try:
            return (
                el.inner_text()
                or el.get_attribute("placeholder")
                or el.get_attribute("value")
                or el.get_attribute("aria-label")
                or ""
            ).strip()
        except Exception:
            return ""

    @staticmethod
    def _is_disabled(el) -> bool:
        """후보 element 가 비활성 상태면 True. 검사 실패 시 False (보수적 — 매칭 시도).

        검사: ``el.disabled`` / ``aria-disabled="true"`` / class 의
        ``-disabled`` 또는 ``disabled`` 토큰.
        """
        try:
            return bool(el.evaluate(
                """(el) => {
                    if (!el) return false;
                    if (el.disabled === true) return true;
                    if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') return true;
                    const cls = el.className || '';
                    const s = typeof cls === 'string' ? cls : (cls.baseVal || '');
                    return /(?:^|\\s)\\S*-disabled(?:\\s|$)|(?:^|\\s)disabled(?:\\s|$)/.test(s);
                }"""
            ))
        except Exception:
            return False
