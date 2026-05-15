import re
import difflib
import logging

from playwright.sync_api import Page, Locator

from .locator_resolver import _IFRAME_SELECTOR_RE

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

    def try_heal(self, step: dict) -> tuple[Locator, str] | None:
        """step의 target과 유사한 요소를 DOM에서 검색한다.

        Returns ``(locator, healed_selector_str) | None``.

        ``healed_selector_str`` 은 매칭된 요소를 *다시 찾아낼 수 있는* DSL 표현
        (``text=<t>`` / ``placeholder=<t>`` / ``label=<t>``) — regression_generator
        가 그대로 Playwright 호출로 재컴파일할 수 있다. 매칭 attribute 가
        ``value`` 처럼 DSL 표현 부재인 케이스는 빈 문자열 반환 (caller 가 원본
        target 으로 fallback).

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

        best_match: Locator | None = None
        best_text = ""
        best_source = ""
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
            text, source = self._extract_text(el)
            if not text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_target, text).ratio()
            if ratio > self.threshold and ratio > highest_ratio:
                highest_ratio = ratio
                best_match = el
                best_text = text
                best_source = source

        if best_match is None:
            return None

        healed_selector = self._build_healed_selector(best_text, best_source)
        log.info(
            "  [로컬복구 성공] 유사도 %.0f%% 매칭 (scope=%s, source=%s, healed=%r)",
            highest_ratio * 100, scope_label, best_source, healed_selector,
        )
        return best_match, healed_selector

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
            # codegen 이 frame entry 를 ``frame=`` prefix 없이 bare
            # ``iframe[...]`` 형태로 emit 한 chain (예: SmartEditor / Naver
            # keditor 의 nested editor) 도 frame scope 로 진입시킨다. resolver
            # 의 _apply_chain_segment 와 동일한 정규식을 공용 — 두 경로의
            # frame scope 인식 기준이 어긋나면 같은 시나리오에서 resolver 는
            # 통과하는데 healer 는 page scope 에 머무는 비대칭이 생긴다.
            if _IFRAME_SELECTOR_RE.match(seg):
                try:
                    cur = cur.frame_locator(seg)
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
    def _extract_text(el) -> tuple[str, str]:
        """매칭에 쓸 텍스트와 그 출처를 함께 반환.

        Returns ``(text, source)`` — ``source`` ∈ ``{"text","placeholder","value",
        "aria_label","title","alt","testid",""}``.

        executor 의 locator_resolver 가 인식하는 6개 semantic prefix
        (text/label/placeholder/testid/title/alt) 와 동기화. 이전엔 text/
        placeholder/aria_label 3종만 probe 해 ``title=`` / ``alt=`` 타깃의 자가치유
        가 사실상 불가능했음. ``value`` 는 DSL 표현 부재로 caller fallback 용도.
        """
        try:
            t = (el.inner_text() or "").strip()
            if t:
                return t, "text"
            p = (el.get_attribute("placeholder") or "").strip()
            if p:
                return p, "placeholder"
            v = (el.get_attribute("value") or "").strip()
            if v:
                return v, "value"
            a = (el.get_attribute("aria-label") or "").strip()
            if a:
                return a, "aria_label"
            ti = (el.get_attribute("title") or "").strip()
            if ti:
                return ti, "title"
            al = (el.get_attribute("alt") or "").strip()
            if al:
                return al, "alt"
            # data-testid — Playwright 의 get_by_test_id 기본 attribute.
            tid = (el.get_attribute("data-testid") or "").strip()
            if tid:
                return tid, "testid"
        except Exception:
            return "", ""
        return "", ""

    @staticmethod
    def _build_healed_selector(text: str, source: str) -> str:
        """매칭 element 의 (text, source) → DSL selector 문자열.

        regression_generator 의 ``_target_to_playwright_code`` 가 이 prefix 들을
        그대로 Playwright 호출로 변환한다.

        - ``text``        → ``text=<t>``       (page.get_by_text)
        - ``placeholder`` → ``placeholder=<t>``(page.get_by_placeholder)
        - ``aria_label``  → ``label=<t>``      (page.get_by_label)
        - ``title``       → ``title=<t>``      (page.get_by_title)
        - ``alt``         → ``alt=<t>``        (page.get_by_alt_text)
        - ``testid``      → ``testid=<t>``     (page.get_by_test_id)
        - ``value`` / 기타 → 빈 문자열 (DSL 표현 부재 — caller fallback)
        """
        if not text:
            return ""
        if source == "text":
            return f"text={text}"
        if source == "placeholder":
            return f"placeholder={text}"
        if source == "aria_label":
            return f"label={text}"
        if source == "title":
            return f"title={text}"
        if source == "alt":
            return f"alt={text}"
        if source == "testid":
            return f"testid={text}"
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
