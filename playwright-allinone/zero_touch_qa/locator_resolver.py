import re
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)

# DSL target prefix constants — shared by _resolve_* and _raw_*.
_ROLE_PREFIX = "role="


class ShadowAccessError(RuntimeError):
    """Encountered a closed shadow root that automation cannot reach.

    T-C (P0.2). Raised when the DSL ``shadow=<host>`` segment points at a Web
    Component attached with mode=closed. The executor catches this and immediately
    FAILs the step / ends the scenario — preventing a 30s timeout hang.
    """


class LocatorResolver:
    """
    7-stage semantic-search engine that converts a Dify-produced target into a Playwright Locator.

    Lookup order:
      1. role + name   (accessibility role; most stable)
      2. text          (visible text)
      3. label         (input-form label)
      4. placeholder   (input-field hint)
      5. testid        (data-testid attribute)
      6. CSS / XPath   (structural fallback)
      7. existence check (return when count > 0, None on failure)
    """

    def __init__(self, page: Page):
        self.page = page
        # Map of selectors healed earlier in the same scenario.
        # e.g. if step 2's fill is healed 'name=query' → 'placeholder=Search',
        # then step 3's press meeting the same 'name=query' immediately
        # tries 'placeholder=Search' first and acts on the same element.
        self.healed_aliases: dict[str, str] = {}

    @staticmethod
    def _safe_count(loc: Locator) -> int:
        """Return element count; return 0 for invalid selectors."""
        try:
            return loc.count()
        except Exception:
            return 0

    def record_alias(self, original, healed) -> None:
        """Record that an original target was healed into a healed one.

        When a later step in the same scenario meets the same ``original``,
        it immediately uses ``healed`` as its first attempt for consistency.
        Ignored if ``original`` is empty.
        """
        if not original or not healed:
            return
        key = str(original).strip()
        val = str(healed).strip()
        if not key or not val or key == val:
            return
        if self.healed_aliases.get(key) != val:
            log.info("[Resolver] alias registered: %s → %s", key, val)
            self.healed_aliases[key] = val

    def resolve(self, target) -> Locator | None:
        """Convert a DSL target into a Playwright Locator.

        7-stage semantic-search order: role→text→label→placeholder→testid→CSS/XPath→existence check.

        Args:
            target: DSL step's target value. A string (``"role=button, name=Sign in"``),
                    dict (``{"role": "button", "name": "OK"}``), or None.

        Returns:
            Matching ``Locator`` (always ``.first``). ``None`` if no element is found.

        T-A (P0.4) extension: handles trailing modifiers in target (`, nth=N` / `, has_text=...`).
        Interprets the 14-DSL options the AST converter emits to preserve codegen's
        ``.nth(N)`` / ``.first`` / ``.filter(has_text=...)`` on the receiver side.
        """
        if not target:
            return None

        # If a healed alias is registered, prefer it
        if isinstance(target, str):
            aliased = self.healed_aliases.get(target.strip())
            if aliased:
                log.debug("[Resolver] using alias: %s → %s", target, aliased)
                target = aliased

        # Dict target (when Dify sent a JSON object)
        if isinstance(target, dict):
            return self._resolve_dict(target)

        target_str = str(target).strip()

        # T-A (P0.4) — extract trailing modifiers (nth, has_text).
        # Split the body selector from the modifiers.
        base_str, modifiers = _split_modifiers(target_str)

        # P0.1 #2 — `>>` chain (e.g. ``#sidebar >> role=button, name=Settings``,
        # ``frame=#x >> role=textbox, name=Card``). Apply each segment cumulatively
        # — the AST converter emits this form to preserve codegen's nested locators.
        if " >> " in base_str:
            if modifiers:
                raw = self._resolve_chain(base_str, raw=True)
                if raw is None:
                    return None
                return _apply_modifiers(raw, modifiers)
            return self._resolve_chain(base_str, raw=False)

        if not modifiers:
            # Existing path — return a single-element locator with .first applied.
            loc = self._resolve_role(base_str)
            if loc is None:
                loc = self._resolve_semantic_prefix(base_str)
            if loc is None:
                loc = self._resolve_css_xpath(base_str)
            return loc

        # T-A modifier path — apply nth/filter on a raw multi-element locator.
        # Calling `.nth(N)` on top of `.first` yields an empty result for N≥1 in Playwright.
        raw = self._resolve_raw(base_str)
        if raw is None:
            return None
        return _apply_modifiers(raw, modifiers)

    def _resolve_dict(self, target: dict) -> Locator | None:
        """Resolve a dict-form target by key priority (role/label/text/placeholder/testid).

        Performs a ``count() > 0`` existence check per key to avoid the
        30-second timeout when no element is present.
        """
        if target.get("role"):
            loc = self.page.get_by_role(
                target["role"], name=target.get("name", "")
            )
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("label"):
            loc = self.page.get_by_label(target["label"])
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("text"):
            loc = self.page.get_by_text(target["text"])
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("placeholder"):
            loc = self.page.get_by_placeholder(target["placeholder"])
            return loc.first if self._safe_count(loc) > 0 else None
        if target.get("testid"):
            loc = self.page.get_by_test_id(target["testid"])
            return loc.first if self._safe_count(loc) > 0 else None
        # Fallback: selector key or string conversion
        fallback = target.get("selector", str(target))
        return self._resolve_css_xpath(str(fallback).strip())

    # Without a name qualifier, the first page-wide match (typically header/logo)
    # is selected, producing a false-positive PASS where the action lands on the
    # wrong element. We reject these broad roles and force the use of fallback_targets.
    _AMBIGUOUS_ROLES_WITHOUT_NAME = {
        "link", "button", "textbox", "checkbox", "radio",
        "searchbox", "combobox", "menuitem", "tab", "option",
    }

    def _resolve_role(self, target_str: str) -> Locator | None:
        """Resolve targets prefixed with ``role=`` via get_by_role.

        Performs a ``count() > 0`` existence check so that when no element is
        present we return None immediately without the 30-second timeout.

        Reject broad roles (link, button, etc.) without a ``name=`` qualifier —
        prevents the false-positive PASS where 'first search result link' intent
        wrongly matches the page header/logo. Use fallback_targets if present.

        T-H (B) — when ``role=X, name=Y`` matches multiple elements (sites with
        mobile drawer + desktop GNB sharing the same label), **prefer the visible
        match**. If all are hidden, fall back to ``.first`` as before (Visibility
        Healer handles the rest).
        """
        if not target_str.startswith(_ROLE_PREFIX):
            return None
        m = re.match(r"role=(.+?),\s*name=(.+)", target_str)
        if m:
            loc = self.page.get_by_role(
                m.group(1).strip(), name=m.group(2).strip()
            )
            if self._safe_count(loc) == 0:
                return None
            return _prefer_visible(loc)
        # role only, no name
        role_only = target_str.replace(_ROLE_PREFIX, "", 1).strip()
        # Composite selectors like "role=link, text=X" → extract only the role part
        if "," in role_only:
            role_only = role_only.split(",", 1)[0].strip()
        if not role_only:
            return None
        if role_only.lower() in self._AMBIGUOUS_ROLES_WITHOUT_NAME:
            log.warning(
                "[Resolver] role=%r has no name= → rejected (broad-match risk). "
                "Use fallback_targets or heuristics.",
                role_only,
            )
            return None
        loc = self.page.get_by_role(role_only)
        return loc.first if self._safe_count(loc) > 0 else None

    def _resolve_semantic_prefix(self, target_str: str) -> Locator | None:
        """Match text= / label= / placeholder= / testid= prefixes and call the matching method.

        Performs a ``count() > 0`` existence check so that when no element is
        present we return None immediately without the 30-second timeout.
        """
        prefix_map = {
            "text=": self.page.get_by_text,
            "label=": self.page.get_by_label,
            "placeholder=": self.page.get_by_placeholder,
            "testid=": self.page.get_by_test_id,
        }
        for prefix, method in prefix_map.items():
            if target_str.startswith(prefix):
                value = target_str.replace(prefix, "", 1).strip()
                loc = method(value)
                return loc.first if self._safe_count(loc) > 0 else None
        return None

    def _resolve_css_xpath(self, target_str: str) -> Locator | None:
        """Find by CSS selector or XPath; return when count > 0."""
        try:
            loc = self.page.locator(target_str)
            if self._safe_count(loc) > 0:
                return loc.first
        except Exception:
            log.debug("CSS/XPath lookup failed: %s", target_str)
        return None

    def _resolve_raw(self, base_str: str) -> Locator | None:
        """For the T-A modifier path. Returns a multi-element Locator without ``.first``.

        Same dispatch order as ``_resolve_role`` / ``_resolve_semantic_prefix`` /
        ``_resolve_css_xpath``, but does not reduce to a single element.
        Called only when modifiers (`nth=N` / `has_text=...`) are present.
        """
        loc = self._raw_role(base_str)
        if loc is None:
            loc = self._raw_semantic_prefix(base_str)
        if loc is None:
            loc = self._raw_css_xpath(base_str)
        return loc

    def _raw_role(self, base_str: str) -> Locator | None:
        """Raw multi-element locator for the ``role=...`` pattern. Skips the
        ambiguous-role rejection because a modifier is explicitly present."""
        if not base_str.startswith(_ROLE_PREFIX):
            return None
        m = re.match(r"role=(.+?),\s*name=(.+)", base_str)
        if m:
            loc = self.page.get_by_role(
                m.group(1).strip(), name=m.group(2).strip()
            )
            return loc if self._safe_count(loc) > 0 else None
        role_only = base_str.replace(_ROLE_PREFIX, "", 1).strip()
        if "," in role_only:
            role_only = role_only.split(",", 1)[0].strip()
        if not role_only:
            return None
        loc = self.page.get_by_role(role_only)
        return loc if self._safe_count(loc) > 0 else None

    def _raw_semantic_prefix(self, base_str: str) -> Locator | None:
        """Raw locator for ``text=`` / ``label=`` / ``placeholder=`` / ``testid=``."""
        prefix_map = {
            "text=": self.page.get_by_text,
            "label=": self.page.get_by_label,
            "placeholder=": self.page.get_by_placeholder,
            "testid=": self.page.get_by_test_id,
        }
        for prefix, method in prefix_map.items():
            if base_str.startswith(prefix):
                value = base_str.replace(prefix, "", 1).strip()
                loc = method(value)
                return loc if self._safe_count(loc) > 0 else None
        return None

    def _raw_css_xpath(self, base_str: str) -> Locator | None:
        """Raw CSS/XPath locator (no ``.first``)."""
        try:
            loc = self.page.locator(base_str)
            if self._safe_count(loc) > 0:
                return loc
        except Exception:
            log.debug("[Resolver] raw CSS/XPath lookup failed: %s", base_str)
        return None

    # ─────────────────────────────────────────────────────────────────────
    # Chain resolution (T-A / P0.1 #2) — nested locators joined by `>>`
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_chain(self, base_str: str, *, raw: bool) -> Locator | None:
        """Resolve segments joined by ``>>`` as a cumulative chain.

        Supported segments:
          - ``frame=<sel>`` → ``page.frame_locator(sel)`` (preferred as the first segment)
          - ``role=<r>`` / ``role=<r>, name=<n>`` → ``cur.get_by_role(...)``
          - ``text=<t>`` / ``label=<t>`` / ``placeholder=<t>`` / ``testid=<t>``
          - Other → ``cur.locator(seg)`` (CSS/XPath fallback)

        In a chain context narrowed by a container, the ambiguous-role
        rejection (name-less ``button`` / ``link``) does not apply. Forms like
        ``#sidebar >> role=button`` already suppress the false-positive risk
        via the container itself.

        Args:
            base_str: selector string containing ``>>`` (without modifiers).
            raw: if True, return a multi-element locator (no ``.first``)
                 for the modifier path; if False, return a single element with ``.first``.

        Returns:
            Matched ``Locator`` or None. None if any intermediate segment is
            unsupported.
        """
        segments = [s.strip() for s in base_str.split(" >> ") if s.strip()]
        if not segments:
            return None

        cur = self.page
        for seg in segments:
            cur = self._apply_chain_segment(cur, seg)
            if cur is None:
                return None

        # frame= alone (cur is a FrameLocator) — cannot be an action target → None.
        # FrameLocator has no .count()/.first, so we identify with hasattr.
        if not hasattr(cur, "first") or not hasattr(cur, "count"):
            return None

        if self._safe_count(cur) == 0:
            return None
        return cur if raw else cur.first

    @staticmethod
    def _apply_chain_segment(cur, seg: str):
        """Apply one segment to the current root (Page / FrameLocator / Locator).

        Returns:
            A new Locator / FrameLocator. None for invalid input.

        Raises:
            ShadowAccessError: when the host element of the ``shadow=<host>``
                segment has a mode=closed shadow root and piercing is impossible.
        """
        if seg.startswith("frame="):
            sel = seg[len("frame="):].strip()
            if not sel:
                return None
            try:
                return cur.frame_locator(sel)
            except Exception:
                return None

        if seg.startswith("shadow="):
            # T-C (P0.2) — explicit shadow host marker. Playwright auto-pierces
            # open shadow, but closed shadow is a 0-match that times out forever.
            # When the user signals intent via shadow=, inspect the host's shadowRoot
            # mode and immediately escalate to ShadowAccessError if closed.
            sel = seg[len("shadow="):].strip()
            if not sel:
                return None
            try:
                host = cur.locator(sel)
                if host.count() == 0:
                    return None
                # If the host's shadowRoot is null, it's either (1) no shadow or
                # (2) closed shadow. If tagName has a hyphen (custom-element naming),
                # we infer (2) — per the Web Components spec this matches 99% of
                # cases when the host is the automation target.
                mode = host.first.evaluate(
                    """el => {
                        if (el.shadowRoot) return 'open';
                        const isCustom = el.tagName && el.tagName.includes('-');
                        return isCustom ? 'closed' : 'none';
                    }"""
                )
            except ShadowAccessError:
                raise
            except Exception:
                return None
            if mode == "closed":
                raise ShadowAccessError(
                    f"closed shadow root — automation impossible (host={sel!r}). "
                    f"By browser policy, automation tools cannot pierce a closed-mode "
                    f"shadow DOM. Modify the app so the component attaches in open mode, "
                    f"or work around it by routing the next step through a frame/popup."
                )
            # open / none — continue. Subsequent segments use host as the scope.
            return host

        if seg.startswith(_ROLE_PREFIX):
            m = re.match(r"role=(.+?),\s*name=(.+)", seg)
            if m:
                return cur.get_by_role(
                    m.group(1).strip(), name=m.group(2).strip(),
                )
            role_only = seg[len(_ROLE_PREFIX):].strip()
            if "," in role_only:
                role_only = role_only.split(",", 1)[0].strip()
            if not role_only:
                return None
            return cur.get_by_role(role_only)

        prefix_map = {
            "text=": "get_by_text",
            "label=": "get_by_label",
            "placeholder=": "get_by_placeholder",
            "testid=": "get_by_test_id",
        }
        for prefix, method_name in prefix_map.items():
            if seg.startswith(prefix):
                value = seg[len(prefix):].strip()
                method = getattr(cur, method_name, None)
                if method is None:
                    return None
                return method(value)

        # CSS/XPath fallback
        try:
            return cur.locator(seg)
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────
# Modifier handling (T-A / P0.4)
# ─────────────────────────────────────────────────────────────────────────

# Trailing nth/has_text modifier parsing — distinguished from commas inside the
# base selector (e.g. 'role=link, name=Top story'). Match only by modifier key prefix.
# T-H (B) — among multiple matches, prefer the visible one. On sites like ktds.com
# where mobile drawer (earlier in DOM order) + desktop GNB both share the same label,
# this prevents `.first` from grabbing the hidden mobile drawer and click-timing-out.
def _prefer_visible(loc: "Locator") -> "Locator":
    """Return the first visible element among multiple matches; ``.first`` if no visible match.

    Uses Playwright 1.36+'s ``filter(visible=True)`` — applies the visibility
    filter to the matches themselves (not descendants). count() == 0 means all
    are hidden, so we fall back to ``.first`` (the Visibility Healer handles
    the rest).
    """
    try:
        visible = loc.filter(visible=True)
        if visible.count() > 0:
            return visible.first
    except Exception:
        pass
    return loc.first


_MODIFIER_KEYS = ("nth", "has_text")


def _split_modifiers(target_str: str) -> tuple[str, list[tuple[str, str]]]:
    """Split ``, nth=N`` / ``, has_text=T`` from the end of the target string.

    ``role=link, name=Top, nth=1, has_text=Main`` → base=``role=link, name=Top``,
    modifiers=[(``nth``, ``1``), (``has_text``, ``Main``)].

    ``, name=...`` inside the base string is not a modifier and is preserved.
    """
    parts = target_str.split(", ")
    modifiers: list[tuple[str, str]] = []
    while parts:
        last = parts[-1]
        if "=" not in last:
            break
        key, _, value = last.partition("=")
        key = key.strip()
        if key not in _MODIFIER_KEYS:
            break
        modifiers.append((key, value.strip()))
        parts.pop()
    modifiers.reverse()
    return ", ".join(parts), modifiers


def _apply_modifiers(
    loc: Locator, modifiers: list[tuple[str, str]],
) -> Locator | None:
    """Apply nth(N) / filter(has_text=T) in order. Return None on bad arguments."""
    for key, value in modifiers:
        try:
            if key == "nth":
                idx = int(value)
                # nth(-1) is equivalent to last() — Playwright supports negatives
                loc = loc.nth(idx)
            elif key == "has_text":
                loc = loc.filter(has_text=value)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "[Resolver] failed to apply modifier (%s=%s): %s",
                key, value, e,
            )
            return None
    return loc
