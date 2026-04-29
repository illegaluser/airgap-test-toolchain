import re
import difflib
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)


class LocalHealer:
    """
    Without an LLM call (zero cost), scan the current page's DOM and return
    the element most similar to the failed target.

    Per-action search scope:
      - fill/press:      input, textarea, [role='textbox'], [role='searchbox'], [contenteditable]
      - select:          select, [role='listbox'], [role='combobox'], option, [role='option']
      - hover:           button, a, [role='menuitem'], [role='tab'], nav a, [aria-haspopup]
      - click/check etc: button, a, [role='button'], [role='link'], [role='menuitem'], [role='tab']
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
        """Search the DOM for an element similar to the step's target.

        T-C (P0.2) — when target starts with a ``frame=<sel> >> ...`` chain,
        only try fallback inside the same FrameLocator and do not cross frame
        boundaries. Otherwise scan the whole page as before.
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
            text = self._extract_text(el)
            if not text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_target, text).ratio()
            if ratio > self.threshold and ratio > highest_ratio:
                highest_ratio = ratio
                best_match = el

        if best_match:
            log.info(
                "  [local-heal success] similarity %.0f%% match (scope=%s)",
                highest_ratio * 100, scope_label,
            )
        return best_match

    def _frame_scope_for_target(self, target):
        """If target starts with ``frame=<sel>`` return that FrameLocator, else page.

        The returned ``(scope, label)`` has ``scope`` as something that supports
        ``.locator(...)`` (Page or FrameLocator). label is for logging only.
        """
        if not isinstance(target, str):
            return self.page, "page"
        # Inspect the first segment of a composite chain. For nested frames the
        # second segment may also start with frame=, so we accumulate.
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
        """Strip semantic prefixes and extract the plain text.

        T-C (P0.2) — for a ``frame=...>>`` chain, use only the last segment.
        Frame-scoped fallback by the healer must do text matching against the
        leaf descriptor to be meaningful.

        For ``role=X, name=Y`` patterns, extract only the human-readable ``Y``
        (X is an accessibility role and adds nothing to text matching). Trailing
        modifiers (``, nth=N`` / ``, has_text=T``) are stripped first so this
        works as-is on the form the converter AST emits.
        """
        s = str(target)
        if " >> " in s:
            s = s.split(" >> ")[-1]
        # Trailing modifier (nth / has_text) — noise for text matching.
        s = re.sub(r",\s*(nth|has_text)=.*$", "", s).strip()
        # `role=X, name=Y` → Y. The accessibility role itself is text noise.
        m = re.match(r"role=.+?,\s*name=(.+)$", s)
        if m:
            return m.group(1).strip()
        # A bare role= (no name) cannot be text-matched — return empty (try_heal skips).
        if s.startswith("role="):
            return ""
        # text/label/placeholder/testid/frame/shadow standalone prefixes are safe to strip.
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
