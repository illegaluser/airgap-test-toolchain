import json
import os
import random
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Page, Locator, expect

from .auth import (
    AuthOptions,
    Credential,
    CredentialError,
    EMAIL_FIELD_CANDIDATES,
    PASSWORD_FIELD_CANDIDATES,
    SUBMIT_BUTTON_CANDIDATES,
    TOTP_FIELD_CANDIDATES,
    generate_totp_code,
    mask_secret,
    parse_auth_target,
    resolve_credential,
)
from .config import Config
from .dify_client import DifyClient, DifyConnectionError
from .locator_resolver import LocatorResolver, ShadowAccessError
from .local_healer import LocalHealer

log = logging.getLogger(__name__)


# Letting the Healer change the action itself carries a high false-PASS risk, so we
# only allow a narrow set of semantically equivalent transitions. Cross-group transitions
# (e.g. navigate <-> verify, drag -> click) defeat the intended verification entirely
# and are never permitted. Kept 1:1 in sync with the Healer system prompt in
# dify-chatflow.yaml.
_HEAL_ACTION_TRANSITIONS = frozenset({
    ("select", "fill"), ("fill", "select"),
    ("check", "click"), ("click", "check"),
    ("click", "press"), ("press", "click"),
    ("upload", "click"), ("click", "upload"),
})


def _is_allowed_action_transition(old_action: str, new_action: str) -> bool:
    """Check whether a Healer-proposed action change is on the whitelist."""
    if not isinstance(old_action, str) or not isinstance(new_action, str):
        return False
    if old_action == new_action:
        return True
    return (old_action.lower(), new_action.lower()) in _HEAL_ACTION_TRANSITIONS


class VerificationAssertionError(AssertionError):
    """Raised when element resolution succeeded but the verify condition did not match."""


@dataclass
class _StrategyAttempt:
    """Result of a single strategy attempt. Empty ``error`` means the strategy PASSed."""
    name: str
    error: str = ""

    def to_dict(self) -> dict:
        return {"strategy": self.name, "error": self.error or "ok"}


@dataclass
class StepResult:
    """Dataclass holding the execution result of a single DSL step.

    Attributes:
        step_id: Step number or identifier within the scenario.
        action: Name of the DSL action performed (click, fill, navigate, etc.).
        target: Locator string actually used.
        value: Value passed to the action (input text, URL, key name, etc.).
        description: Human-readable description of the step.
        status: Execution result. ``"PASS"`` | ``"HEALED"`` | ``"FAIL"`` | ``"SKIP"``.
        heal_stage: Stage at which the step was recovered. ``"none"`` | ``"fallback"`` | ``"local"`` | ``"dify"``.
        timestamp: Step execution time (Unix epoch).
        screenshot_path: Screenshot file path. ``None`` if absent.
    """

    step_id: int | str
    action: str
    target: str
    value: str
    description: str
    status: str  # "PASS" | "HEALED" | "FAIL" | "SKIP"
    heal_stage: str = "none"  # "none" | "fallback" | "local" | "dify"
    timestamp: float = field(default_factory=time.time)
    screenshot_path: str | None = None


# Visibility Healer (T-H) JS — extracts hoverable candidates from the element's
# ancestor chain.
# Priority: aria-haspopup > aria-expanded=false > role=menu/menubar/listbox/tooltip/combobox >
#          tag=nav/details/summary > [data-state=closed] / [hidden] toggleable > :hover CSS rule.
# Each candidate is returned with a stable CSS path (id first -> nth-of-type chain).
_VISIBILITY_HEALER_JS = r"""
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

  // Check whether this is the trigger of a :hover CSS rule. If selectorText is
  // 'A:hover B', the trigger is A — the node is hoverable iff it matches A.
  // 'ul#gnb > li:hover > .submenu' -> trigger = `ul#gnb > li`.
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


# T-H (G) — Safety guard for the JS dispatchEvent('click') fallback.
# Only allow anchor/button/input/role=button/role=link/role=menuitem. Firing a JS
# click on a plain div risks a false-positive PASS because the real site has no listener.
def _is_safe_for_js_click(locator) -> bool:
    """JS click is safe iff the element is an anchor/button/clickable role; otherwise raise."""
    try:
        info = locator.evaluate(
            """el => ({
                tag: (el.tagName || '').toLowerCase(),
                role: el.getAttribute && el.getAttribute('role'),
                onclick: typeof el.onclick === 'function',
            })"""
        )
    except Exception:
        return False
    tag = info.get("tag")
    role = (info.get("role") or "").lower()
    if tag in ("a", "button"):
        return True
    if tag == "input" and role in ("button", "submit", ""):
        return True
    if role in ("button", "link", "menuitem", "tab", "option", "checkbox"):
        return True
    if info.get("onclick"):
        return True
    return False


def _dump_storage_state(context, path: str) -> None:
    """Dump the current BrowserContext's storage_state to ``path`` as JSON (T-D / P0.1).

    On failure only logs a warning — does not affect scenario execution results.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        context.storage_state(path=path)
        log.info("[Auth] storage_state dump complete — %s", path)
    except Exception as e:  # noqa: BLE001
        log.warning("[Auth] storage_state dump failed (%s): %s", path, e)


def _apply_fingerprint_env(context_kwargs: dict) -> None:
    """Apply auth-profile fingerprint env overrides onto ``context_kwargs`` (P4.1).

    Design: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.8 (D10).

    When ``replay_proxy`` injects the fingerprint captured at seed time (viewport /
    locale / timezone / color_scheme) via env vars, this function overrides the
    defaults. UA is intentionally omitted — to avoid mismatches with sec-ch-ua
    Client Hints we do not spoof it.

    Effect:
        - ``PLAYWRIGHT_VIEWPORT``      = ``"<W>x<H>"`` (e.g. ``1280x800``)
        - ``PLAYWRIGHT_LOCALE``        = locale string (e.g. ``ko-KR``)
        - ``PLAYWRIGHT_TIMEZONE``      = IANA timezone (e.g. ``Asia/Seoul``)
        - ``PLAYWRIGHT_COLOR_SCHEME``  = ``"light"`` / ``"dark"`` / ``"no-preference"``
    """
    viewport_env = os.environ.get("PLAYWRIGHT_VIEWPORT", "")
    if viewport_env and "x" in viewport_env:
        try:
            w_str, h_str = viewport_env.split("x", 1)
            context_kwargs["viewport"] = {"width": int(w_str), "height": int(h_str)}
        except (ValueError, IndexError):
            log.warning(
                "[Auth] PLAYWRIGHT_VIEWPORT malformed (ignored) — %r", viewport_env,
            )
    locale_env = os.environ.get("PLAYWRIGHT_LOCALE")
    if locale_env:
        context_kwargs["locale"] = locale_env
    timezone_env = os.environ.get("PLAYWRIGHT_TIMEZONE")
    if timezone_env:
        context_kwargs["timezone_id"] = timezone_env
    color_env = os.environ.get("PLAYWRIGHT_COLOR_SCHEME")
    if color_env:
        context_kwargs["color_scheme"] = color_env


class QAExecutor:
    """
    Execute a DSL scenario and run a three-stage hybrid self-healing loop.

    Healing loop:
      1. Iterate fallback_targets (zero cost).
      2. LocalHealer DOM similarity matching.
      3. DifyClient LLM healing.
    """

    def __init__(self, config: Config):
        self.config = config
        self.dify = DifyClient(config)
        # A: Record of the strategy-chain attempts from the previous step. Reset on
        # _perform_action entry. Injected into the Dify healer call as LLM context so
        # the LLM can see signals like "same timeout even when only the selector changed".
        self._latest_strategy_trace: list[_StrategyAttempt] = []

    def execute(
        self,
        scenario: list[dict],
        headed: bool = True,
        storage_state_in: Optional[str] = None,
        storage_state_out: Optional[str] = None,
    ) -> list[StepResult]:
        """Launch a Playwright browser and execute the DSL scenario sequentially.

        Args:
            scenario: List of DSL step dicts.
            headed: True shows the browser window, False runs headless.
            storage_state_in: Path to a previously dumped storage_state JSON —
                restores the post-auth session into a new context (T-D / P0.1).
                If None, falls back to env ``AUTH_STORAGE_STATE_IN``; if that is
                also unset, a fresh context is used.
            storage_state_out: Path to which the current context's storage_state
                will be dumped after the scenario finishes. If None, falls back
                to env ``AUTH_STORAGE_STATE_OUT``; if that is also unset, no dump
                is performed.

        Returns:
            List of ``StepResult`` for each step. On FAIL, subsequent steps are not included.
        """
        results: list[StepResult] = []
        artifacts = self.config.artifacts_dir
        os.makedirs(artifacts, exist_ok=True)

        # T-D / P0.1 — resolve storage_state paths (arg first, env fallback).
        if storage_state_in is None:
            storage_state_in = os.environ.get("AUTH_STORAGE_STATE_IN") or None
        if storage_state_out is None:
            storage_state_out = os.environ.get("AUTH_STORAGE_STATE_OUT") or None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed, slow_mo=self.config.slow_mo)
            context_kwargs: dict = {
                "locale": "ko-KR",
                "viewport": {
                    "width": self.config.viewport[0],
                    "height": self.config.viewport[1],
                },
            }
            # P4.1 — auth-profile fingerprint env override (D10).
            # When replay_proxy injects the seed-time fingerprint as env vars, this
            # overrides the defaults in context_kwargs. UA is not spoofed (D10).
            _apply_fingerprint_env(context_kwargs)
            if storage_state_in and os.path.isfile(storage_state_in):
                log.info("[Auth] storage_state restored — %s", storage_state_in)
                context_kwargs["storage_state"] = storage_state_in
            elif storage_state_in:
                log.warning(
                    "[Auth] storage_state_in file missing — proceeding with fresh context (%s)",
                    storage_state_in,
                )
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            resolver = LocatorResolver(page)
            healer = LocalHealer(page, self.config.heal_threshold)

            try:
                for idx, step in enumerate(scenario):
                    result = self._execute_step(
                        page, step, resolver, healer, artifacts
                    )
                    results.append(result)
                    if headed and self.config.headed_step_pause_ms > 0:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                        time.sleep(self.config.headed_step_pause_ms / 1000.0)
                    if result.status == "FAIL":
                        # Final failure screenshot.
                        fail_path = os.path.join(artifacts, "error_final.png")
                        self._safe_screenshot(page, fail_path)
                        break
                    # G-3: Even when the step is judged PASS/HEALED, treat it as FAIL
                    # at this last layer if the current page.url is a bot-block page
                    # (/sorry/, captcha challenge, etc.). Blocks false-positive success
                    # in scenarios that have no verify step.
                    current_url = page.url or ""
                    if self._is_blocked_url(current_url):
                        log.error(
                            "[Step %s] step was judged %s but current URL is a bot-block page: %s",
                            step.get("step", "-"), result.status, current_url,
                        )
                        result.status = "FAIL"
                        fail_path = os.path.join(artifacts, "error_final.png")
                        self._safe_screenshot(page, fail_path)
                        break
                    # N. New-tab detection — when a search form has target=_blank or
                    # uses JS window.open to open results in a new tab/window, the
                    # original page is unchanged. We must switch here to apply
                    # subsequent steps to the new page.
                    #
                    # O. chrome-error/about:blank filter — when a new tab is an error
                    # page due to a network failure or bot block, do not switch and
                    # ignore (no valid content). G-3 extension: do not switch when the
                    # new tab URL is a bot-block page either.
                    if len(context.pages) > 1 and context.pages[-1] is not page:
                        new_page = context.pages[-1]
                        try:
                            new_page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except Exception:
                            pass
                        new_url = new_page.url
                        if new_url.startswith(("chrome-error://", "about:blank", "data:text/html")):
                            log.warning(
                                "[Step %s] new tab is error/blank page (%s) — not switching. "
                                "Site is blocking Playwright as a bot, or network issue.",
                                step.get("step", "-"), new_url,
                            )
                        elif self._is_blocked_url(new_url):
                            log.error(
                                "[Step %s] new tab is a bot-block page (%s) — not switching, marking step FAIL.",
                                step.get("step", "-"), new_url,
                            )
                            result.status = "FAIL"
                            fail_path = os.path.join(artifacts, "error_final.png")
                            self._safe_screenshot(page, fail_path)
                            break
                        else:
                            log.info(
                                "[Step %s] new tab detected -> switching active page (%s -> %s)",
                                step.get("step", "-"),
                                page.url, new_url,
                            )
                            page = new_page
                            try:
                                page.bring_to_front()
                            except Exception:
                                pass
                            # Rebind the resolver/healer's internal page reference.
                            resolver.page = page
                            healer.page = page
                    # Random jitter between steps — avoid bot patterns (immediate
                    # back-to-back actions). reCAPTCHA etc. trigger on fill->press
                    # sequences under 100ms. Skip sleep on the last step or when max==0.
                    if (
                        idx < len(scenario) - 1
                        and self.config.step_interval_max_ms > 0
                    ):
                        jitter_s = random.uniform(
                            self.config.step_interval_min_ms,
                            self.config.step_interval_max_ms,
                        ) / 1000.0
                        time.sleep(jitter_s)

                # P-1. final_state.png after all steps — when the last click opens
                # a new tab and the page switches, the existing step_N_*.png only
                # captures the screen right before the switch. Capture the final
                # active page state here as visual evidence of "where we actually
                # ended up".
                try:
                    page.bring_to_front()
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                final_path = os.path.join(artifacts, "final_state.png")
                self._safe_screenshot(page, final_path)
                log.info("[Final] final active page: %s -> %s", page.url, final_path)

                # P-2. In headed mode, briefly wait before browser.close() so the
                # user can visually confirm.
                if headed:
                    time.sleep(3)
            finally:
                # T-D / P0.1 — dump storage_state (before browser close, to preserve post-auth session).
                if storage_state_out:
                    _dump_storage_state(context, storage_state_out)
                browser.close()

        return results

    def _execute_step(
        self,
        page: Page,
        step: dict,
        resolver: LocatorResolver,
        healer: LocalHealer,
        artifacts: str,
    ) -> StepResult:
        """Execute a single step and return the result.

        Three-stage self-healing order: 1) fallback_targets -> 2) LocalHealer DOM similarity -> 3) Dify LLM.
        """
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")

        # ── meta actions (no target needed) ──
        if action in ("navigate", "maps"):
            raw_url = step.get("value") or step.get("target", "")
            url = self._normalize_url(str(raw_url))
            if url != str(raw_url):
                log.info("[Step %s] URL auto-normalized: %r -> %r", step_id, raw_url, url)
            # wait_until="domcontentloaded": proceed once the DOM is ready instead
            # of waiting for ads/trackers. Avoids 30s 'load' event timeouts on
            # heavy pages like yahoo.com. Bumped timeout to 60s as well.
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] navigate -> PASS", step_id)
            return StepResult(
                step_id, action, str(url), str(url), desc,
                "PASS", screenshot_path=ss,
            )

        if action == "wait":
            ms = int(step.get("value", 1000))
            page.wait_for_timeout(ms)
            log.info("[Step %s] wait %dms -> PASS", step_id, ms)
            return StepResult(step_id, action, "", str(ms), desc, "PASS")

        # ── LLM output normalization ──
        self._normalize_step(step)
        action = step["action"].lower()

        # ── press without target: send key to whole page ──
        if action == "press" and not step.get("target"):
            key = step.get("value", "")
            page.keyboard.press(key)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] press '%s' (keyboard) -> PASS", step_id, key)
            return StepResult(
                step_id, action, "", key, desc,
                "PASS", screenshot_path=ss,
            )

        if action in ("mock_status", "mock_data"):
            return self._execute_mock_step(page, step, artifacts)

        if action == "auth_login":
            return self._execute_auth_login(page, step, artifacts)

        if action == "reset_state":
            return self._execute_reset_state(page, step, artifacts)

        # ── target-bearing actions: execute + multi-stage self-healing ──
        log.info("[Step %s] %s: %s", step_id, action, desc)
        original_target = step.get("target")
        verification_error: VerificationAssertionError | None = None

        # 1st attempt: original target (Resolver auto-applies healed_aliases).
        # T-C (P0.2) — hitting a closed shadow makes auto-healing pointless and risks
        # a 30s timeout. ShadowAccessError escalates to FAIL immediately, before
        # entering fallback / healer.
        try:
            locator = resolver.resolve(original_target)
        except ShadowAccessError as e:
            log.error("[Step %s] %s", step_id, e)
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return StepResult(
                step_id, action, str(original_target or ""),
                str(step.get("value", "")), f"{desc} [closed shadow]",
                "FAIL", screenshot_path=ss,
            )
        if locator:
            # T-H (Visibility Healer) — if the element is hidden, try ancestor
            # hover; if that fails, swap to a visible sibling match. Covers
            # dropdown menus / hover menus / mobile drawers where the codegen
            # source omits a hover step, or the selector matches both mobile and
            # desktop and the hidden one is picked. Only triggers when matched
            # but not visible.
            swap = self._heal_visibility(page, locator, step_id)
            if swap is not None:
                locator = swap
            try:
                self._perform_action(page, locator, step, resolver)
                ss = self._screenshot(page, artifacts, step_id, "pass")
                return StepResult(
                    step_id, action, str(original_target or ""),
                    str(step.get("value", "")), desc,
                    "PASS", screenshot_path=ss,
                )
            except VerificationAssertionError as e:
                verification_error = e
                log.warning("[Step %s] verify condition failed: %s", step_id, e)
            except Exception as e:
                log.warning("[Step %s] original target failed: %s", step_id, e)

        # ── [heal stage 1] fallback_targets ──
        for fb_target in step.get("fallback_targets", []):
            fb_loc = resolver.resolve(fb_target)
            if fb_loc:
                try:
                    self._perform_action(page, fb_loc, step, resolver)
                    # A: subsequent steps hitting the same target use fb_target immediately.
                    resolver.record_alias(original_target, fb_target)
                    # S2-12: update the step dict itself so scenario.healed.json
                    # records the fallback healing result. Since step is a member
                    # of the scenario list, in-place mutation serializes straight
                    # into healed.json.
                    step["target"] = fb_target
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] fallback recovery succeeded: %s", step_id, fb_target)
                    return StepResult(
                        step_id, action, str(fb_target),
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="fallback", screenshot_path=ss,
                    )
                except VerificationAssertionError as e:
                    verification_error = e
                    log.warning("[Step %s] fallback verify condition failed: %s", step_id, e)
                except Exception:
                    continue

        # ── [heal stage 2] DSL action_alternatives (C) ──
        # Equivalent actions explicitly declared by the planner LLM (e.g. press
        # Enter -> click search button). Tried before LocalHealer/Dify heal —
        # explicit intent is the most trustworthy signal.
        for alt in step.get("action_alternatives", []) or []:
            if not isinstance(alt, dict) or not alt.get("action"):
                continue
            alt_step = {**step, **alt}
            self._normalize_step(alt_step)
            alt_loc = resolver.resolve(alt_step.get("target"))
            if not alt_loc:
                continue
            try:
                self._perform_action(page, alt_loc, alt_step, resolver)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info(
                    "[Step %s] action_alternatives recovery succeeded: %s %s",
                    step_id, alt_step.get("action"), alt_step.get("target"),
                )
                return StepResult(
                    step_id, alt_step.get("action", action),
                    str(alt_step.get("target", "")),
                    str(alt_step.get("value", "")), desc,
                    "HEALED", heal_stage="alternative", screenshot_path=ss,
                )
            except VerificationAssertionError as e:
                verification_error = e
                log.warning(
                    "[Step %s] action_alternatives verify condition failed: %s", step_id, e
                )
            except Exception:
                continue

        if verification_error:
            ss = self._screenshot(page, artifacts, step_id, "fail")
            log.error("[Step %s] FAIL — verify condition mismatch", step_id)
            return StepResult(
                step_id, action, str(original_target or ""),
                str(step.get("value", "")), desc,
                "FAIL", screenshot_path=ss,
            )

        # ── [heal stage 3] local DOM similarity matching ──
        healed_loc = healer.try_heal(step)
        if healed_loc:
            try:
                self._perform_action(page, healed_loc, step, resolver)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] LocalHealer DOM-similarity recovery succeeded", step_id)
                return StepResult(
                    step_id, action, str(original_target or ""),
                    str(step.get("value", "")), desc,
                    "HEALED", heal_stage="local", screenshot_path=ss,
                )
            except Exception as e:
                log.warning("[Step %s] local heal execution failed: %s", step_id, e)

        # ── [heal stage 4] Dify LLM healing (shorter timeout, retry 0) ──
        log.info("[Step %s] Dify LLM heal request in flight (timeout=%ds)...",
                 step_id, self.config.heal_timeout_sec)
        try:
            dom_snapshot = page.content()[: self.config.dom_snapshot_limit]
            # B: inject the previous strategy chain's attempts/failures into the
            # healer prompt. Tells the LLM things like "even after changing only
            # the selector the timeout was the same".
            new_target_info = self.dify.request_healing(
                error_msg=f"element resolution/execution failed: {original_target}",
                dom_snapshot=dom_snapshot,
                failed_step=step,
                strategy_trace=[a.to_dict() for a in self._latest_strategy_trace],
            )
        except DifyConnectionError as e:
            log.error("[Step %s] Dify heal communication failed: %s", step_id, e)
            new_target_info = None

        if new_target_info:
            # B: target / value / condition / fallback_targets are freely mutable.
            # action change is allowed only along _HEAL_ACTION_TRANSITIONS whitelist
            # transitions (Sprint 6 Option-2). Other keys are ignored. Kept 1:1 in
            # sync with the Healer prompt in dify-chatflow.yaml.
            allowed_keys = {"target", "value", "condition", "fallback_targets"}
            mutation = {k: v for k, v in new_target_info.items() if k in allowed_keys}
            proposed_action = new_target_info.get("action")
            if isinstance(proposed_action, str) and proposed_action.strip():
                proposed_action = proposed_action.strip().lower()
                old_action = str(step.get("action", "")).lower()
                if _is_allowed_action_transition(old_action, proposed_action):
                    if proposed_action != old_action:
                        log.warning(
                            "[Step %s] Healer action transition allowed: %s -> %s (whitelist)",
                            step_id, old_action, proposed_action,
                        )
                    mutation["action"] = proposed_action
                else:
                    log.warning(
                        "[Step %s] Healer action transition rejected: %s -> %s (off-whitelist, false-PASS risk)",
                        step_id, old_action, proposed_action,
                    )
            step.update(mutation)
            healed_loc = resolver.resolve(step.get("target"))
            if healed_loc:
                try:
                    # B3: enforce post-condition — _perform_action's strategy chain
                    # has built-in post-checks, so a successful call here means
                    # semantic verification has also passed automatically.
                    self._perform_action(page, healed_loc, step, resolver)
                    resolver.record_alias(original_target, step.get("target"))
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info(
                        "[Step %s] LLM heal succeeded. New target: %s",
                        step_id, step.get("target"),
                    )
                    return StepResult(
                        step_id, str(step.get("action", action)),
                        str(step.get("target", "")),
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="dify", screenshot_path=ss,
                    )
                except Exception as e:
                    log.error("[Step %s] execution after LLM heal failed: %s", step_id, e)

        # ── [heal stage 5] press(Enter/Return) heuristic — click search button (B) ──
        # When a human can't get Enter to work, they click the search button.
        # This last safety net rescues PASS most often on Naver/Google-style
        # search pages.
        #
        # E-1: success of the click alone is not enough. In a "search" intent
        # context, also confirm a navigation effect (URL change or valid new tab)
        # after the click, so chrome-error new-tab artifacts from bot blocks
        # don't slip through as false PASS.
        if action == "press" and str(step.get("value", "")).lower() in ("enter", "return"):
            needs_nav_check = bool(re.search(r"검색|search", desc, re.IGNORECASE))
            for sel in self._SEARCH_BUTTON_CANDIDATES:
                try:
                    btn = page.locator(sel)
                    if btn.count() == 0:
                        continue
                    before_url = page.url
                    before_pages_count = len(page.context.pages)
                    btn.first.click(timeout=3000)
                    if needs_nav_check and not self._wait_for_navigation_effect(
                        page, before_url, before_pages_count
                    ):
                        log.warning(
                            "[Step %s] no valid navigation after press->click — trying next candidate (sel=%s)",
                            step_id, sel,
                        )
                        continue
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] press->click heuristic succeeded: %s", step_id, sel)
                    return StepResult(
                        step_id, "click", sel, "",
                        desc, "HEALED",
                        heal_stage="press_to_click", screenshot_path=ss,
                    )
                except Exception:
                    continue

        # ── [heal stage 6] click "first result/link/item" semantic heuristic (E) ──
        # Even when every site-specific selector the LLM guessed misses, if the
        # description carries an intent like "first search result link", try the
        # first visible link inside main/article. Last-resort safety net for
        # search results on sites like Naver/Google/Yahoo.
        #
        # E-4: success of the click alone is not enough. A "first result" click
        # is intrinsically a navigation to another page, so a URL change or a
        # valid new tab must be confirmed. Blocks the false positive (build #21
        # trending nav) where on Yahoo's home a stray link inside the search form
        # matches and ends as "HEALED".
        if action == "click" and self._matches_first_result_intent(desc):
            for sel in self._FIRST_RESULT_CANDIDATES:
                try:
                    loc = page.locator(sel)
                    if loc.count() == 0:
                        continue
                    before_url = page.url
                    before_pages_count = len(page.context.pages)
                    loc.first.click(timeout=3000)
                    if not self._wait_for_navigation_effect(
                        page, before_url, before_pages_count
                    ):
                        log.warning(
                            "[Step %s] no navigation after 'first result' candidate click — trying next candidate (sel=%s)",
                            step_id, sel,
                        )
                        continue
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] 'first result' heuristic succeeded: %s", step_id, sel)
                    return StepResult(
                        step_id, action, sel, "",
                        desc, "HEALED",
                        heal_stage="first_result", screenshot_path=ss,
                    )
                except Exception:
                    continue

        # ── [heal stage ?] verify "search results present" semantic heuristic (J) ──
        # When the description matches "verify search results (list/present/visible)"
        # AND the action is verify, treat any visible main/article/search-result
        # container as PASS. Routes around an LLM's wrong target/value guess via
        # semantics.
        if action == "verify" and self._matches_search_results_intent(desc):
            for sel in self._SEARCH_RESULTS_CANDIDATES:
                try:
                    loc = page.locator(sel)
                    if loc.count() == 0:
                        continue
                    if loc.first.is_visible():
                        ss = self._screenshot(page, artifacts, step_id, "healed")
                        log.info("[Step %s] 'search results present' heuristic succeeded: %s", step_id, sel)
                        return StepResult(
                            step_id, action, sel,
                            str(step.get("value", "")), desc,
                            "HEALED", heal_stage="search_results_visible",
                            screenshot_path=ss,
                        )
                except Exception:
                    continue

        # ── [heal stage 7] fill "search box" semantic heuristic (H) ──
        # When the LLM's site-specific search-box name/id guess misses (e.g.
        # Yahoo's textarea[name=q]), if the description has a "search" keyword
        # fall back to generic search input selectors.
        # Order: input[type=search] / [role=searchbox] / placeholder/aria-label
        # match / common name attribute values.
        if action == "fill" and self._matches_search_input_intent(desc):
            for sel in self._SEARCH_INPUT_CANDIDATES:
                try:
                    loc = page.locator(sel)
                    if loc.count() == 0:
                        continue
                    loc.first.fill(str(step.get("value", "")))
                    resolver.record_alias(original_target, sel)
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] 'search box' heuristic succeeded: %s", step_id, sel)
                    return StepResult(
                        step_id, action, sel,
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="search_input",
                        screenshot_path=ss,
                    )
                except Exception:
                    continue

        # ── all heal stages exhausted ──
        log.error("[Step %s] FAIL — all heal stages exhausted", step_id)
        return StepResult(
            step_id, action, str(original_target or ""),
            str(step.get("value", "")), desc,
            "FAIL",
        )

    # B: Search/submit button candidates to click when press(Enter) failed every
    # heal stage. Considers visibility filter and Korean/English labels together.
    # Priority is narrow -> broad.
    _SEARCH_BUTTON_CANDIDATES = (
        "form[role=search] button:visible, [role=search] button:visible",
        "button[type=submit]:visible",
        "button[aria-label*='검색']:visible, button[aria-label*='Search' i]:visible",
        "button:has-text(/^(검색|Search|검색하기|Go|확인|Submit)$/i):visible",
        "[role=button]:has-text(/^(검색|Search|검색하기)$/i):visible",
    )

    # E: Intent-matching regex for "first/Nth search result/link/item" (Korean/English).
    # Pattern: ordinal (첫/1번째/first/1st) ... result/link/item, with up to 30
    # characters in between.
    _FIRST_RESULT_RE = re.compile(
        r"(첫\s*번?째|\d+\s*번\s*째?|first|1st)"
        r".{0,30}?"
        r"(검색\s*결과|결과|링크|항목|아이템|result|link|item)",
        re.IGNORECASE | re.DOTALL,
    )

    @staticmethod
    def _matches_first_result_intent(desc: str) -> bool:
        """Detect 'first/Nth result/link/item' intent in the description."""
        return bool(QAExecutor._FIRST_RESULT_RE.search(desc or ""))

    # E: Generic selector candidates to try for 'click first result' intent.
    # Order: per-search-engine accurate result containers -> generic semantic ->
    # broad fallback. (Search-engine containers are more precise than main —
    # avoids recommended-news/ad cards.)
    _FIRST_RESULT_CANDIDATES = (
        "#main_pack a[href]:visible",       # Naver integrated search area
        "#search a[href]:visible",           # Google search area
        "#web a[href]:visible",              # Yahoo search area
        "#results a[href]:visible",          # generic
        "[id*='result' i] a[href]:visible",
        "[class*='result' i] a[href]:visible",
        "[id*='search' i] a[href]:visible",
        "[class*='search' i] a[href]:visible",
        "main a[href]:visible",              # semantic fallback
        "[role=main] a[href]:visible",
        "article a[href]:visible",
        "[role=article] a[href]:visible",
    )

    # H: Intent-matching regex for "fill the search box" (Korean/English).
    # Prefer noun phrases like search bar / search box for the word "search" to
    # avoid mismatching "research".
    _SEARCH_INPUT_RE = re.compile(
        r"검색\s*(창|박스|필드|입력|어\s*입력)|search\s*(box|bar|input|field)",
        re.IGNORECASE,
    )

    @staticmethod
    def _matches_search_input_intent(desc: str) -> bool:
        """Detect 'fill search box' intent in the description."""
        return bool(QAExecutor._SEARCH_INPUT_RE.search(desc or ""))

    # J: Intent-matching regex for "verify search results (list/present/visible)" (Korean/English).
    _SEARCH_RESULTS_RE = re.compile(
        r"검색\s*결과.*(목록|존재|표시|출력|확인|보이는)|"
        r"search\s*result.*(list|exist|visible|appear|show|display)",
        re.IGNORECASE | re.DOTALL,
    )

    @staticmethod
    def _matches_search_results_intent(desc: str) -> bool:
        """Detect 'verify search results present' intent in the description."""
        return bool(QAExecutor._SEARCH_RESULTS_RE.search(desc or ""))

    # G-1: URL patterns for bot-block / captcha challenge / ratelimit pages.
    # Even if the URL changed, it is not the "intended destination" so we must
    # not count it as success. Representative cases:
    #   - Google bot block    : google.com/sorry/index?continue=... ("unusual traffic")
    #   - Google reCAPTCHA    : /recaptcha/
    #   - Cloudflare challenge: /cdn-cgi/challenge-platform/
    #   - Amazon bot check    : /errors/validateCaptcha, /robot-check
    #   - Generic rate limit  : /blocked, /ratelimit, /too-many-requests, /429
    _BLOCKED_URL_RE = re.compile(
        r"/sorry/"
        r"|/recaptcha/"
        r"|/cdn-cgi/challenge"
        r"|/challenge-platform"
        r"|/errors/validateCaptcha"
        r"|/robot-check"
        r"|/blocked(?:[/?]|$)"
        r"|/ratelimit(?:[/?]|$)"
        r"|/too-many-requests"
        r"|/unusual-traffic"
        r"|[?&]captcha=[^&]+"
        r"|/429(?:[/?]|$)|/403(?:[/?]|$)",
        re.IGNORECASE,
    )

    @staticmethod
    def _is_blocked_url(url: str) -> bool:
        """Return True if the URL is a bot-block / captcha / ratelimit page."""
        return bool(QAExecutor._BLOCKED_URL_RE.search(url or ""))

    # E-2: URL patterns for search-result pages. Either a search-query key in
    # the query string, or a /search/ /results/ /find/ path. If a URL does not
    # match this pattern, we cannot assume "we are on a search-results page".
    _SEARCH_RESULT_URL_RE = re.compile(
        r"[?&](q|p|query|search|keyword|wd|k|term|s|searchterm)=|"
        r"/search[/?]|/results?[/?]|/find[/?]|/web[/?]|/results?$|/search$",
        re.IGNORECASE,
    )

    @staticmethod
    def _had_navigation_effect(
        page: Page, before_url: str, before_pages_count: int
    ) -> bool:
        """Determine whether a click/press actually produced a navigation effect.

        Effect = (a) the current page URL changed, or (b) a valid new tab opened.
        chrome-error:// / about:blank / data: new tabs are bot-block artifacts and
        do not count as an effect.
        G-2: Bot-block (/sorry/ etc.) and captcha challenge URLs also do not count
        as an effect. The URL changed but it is not the "intended destination",
        so false positives are blocked.

        Args:
            page: The currently active Playwright Page.
            before_url: page.url right before the action.
            before_pages_count: Length of context.pages right before the action.

        Returns:
            True if a valid navigation effect occurred.
        """
        current = page.url or ""
        if QAExecutor._is_blocked_url(current):
            return False
        if current != (before_url or ""):
            return True
        pages = page.context.pages
        if len(pages) <= before_pages_count:
            return False
        for pg in pages[before_pages_count:]:
            url = pg.url or ""
            if url.startswith(("chrome-error://", "about:blank", "data:text/html")):
                continue
            if QAExecutor._is_blocked_url(url):
                continue
            return True
        return False

    @staticmethod
    def _wait_for_navigation_effect(
        page: Page, before_url: str, before_pages_count: int,
        deadline_sec: float = 3.0,
    ) -> bool:
        """Poll up to ``deadline_sec`` seconds for a navigation effect."""
        deadline = time.time() + deadline_sec
        while time.time() < deadline:
            if QAExecutor._had_navigation_effect(page, before_url, before_pages_count):
                return True
            page.wait_for_timeout(100)
        return QAExecutor._had_navigation_effect(page, before_url, before_pages_count)

    # J: Container candidates whose visibility implies 'search results present'.
    # Any single one being visible means search results exist.
    #
    # Note: main / [role=main] / article are always visible on the home page
    # before any search, which would yield false-positive PASS (Yahoo home's
    # main matches when search fails). Only treat as valid: search-engine-
    # specific containers carrying "search results" intent, or those whose
    # id/class contains 'result'.
    _SEARCH_RESULTS_CANDIDATES = (
        "#main_pack",                     # Naver integrated search
        "#search",                          # Google search
        "#results",                         # generic
        "#web",                             # Yahoo search results
        "[id*='result' i]",
        "[class*='search-result' i]",
        "[class*='results' i]",
        "[data-testid*='result' i]",
    )

    # H: Generic search-input candidates to try for 'fill search box' intent.
    # Order: semantic (type=search/role=searchbox) first, then placeholder/aria-
    # label match, finally common per-search-engine name attributes (q, p,
    # query, search, wd, etc.).
    _SEARCH_INPUT_CANDIDATES = (
        "input[type=search]:visible",
        "[role=searchbox]:visible",
        "[role=combobox][type=search]:visible",
        "input[placeholder*='Search' i]:visible, input[placeholder*='검색']:visible",
        "input[aria-label*='Search' i]:visible, input[aria-label*='검색']:visible",
        "textarea[aria-label*='Search' i]:visible, textarea[aria-label*='검색']:visible",
        "input[name='q']:visible, input[name='p']:visible, input[name='query']:visible, "
        "input[name='search']:visible, input[name='keyword']:visible, input[name='wd']:visible",
        "form[role=search] input:visible, [role=search] input:visible",
    )

    # ── LLM output normalization ──
    KNOWN_KEYS = {
        "enter", "tab", "escape", "backspace", "delete", "arrowup",
        "arrowdown", "arrowleft", "arrowright", "space", "home", "end",
        "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f6",
        "f7", "f8", "f9", "f10", "f11", "f12",
    }

    # Private network / local IP patterns — auto-normalize uses http instead of https.
    _LOCAL_HOST_PREFIXES = ("localhost", "127.", "0.0.0.0", "10.", "192.168.", "172.16.",
                            "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
                            "172.22.", "172.23.", "172.24.", "172.25.", "172.26.",
                            "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")
    _IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/.*)?$")

    @staticmethod
    def _normalize_url(raw: str) -> str:
        """Auto-prefix https:// (http:// for local hosts) to a URL with no scheme.

        Prevents the 'invalid URL' error from ``page.goto()`` when the user puts
        only ``www.naver.com`` into a Jenkins parameter or the LLM returns a URL
        without a scheme.

        Examples:
            >>> QAExecutor._normalize_url("www.naver.com")
            'https://www.naver.com'
            >>> QAExecutor._normalize_url("localhost:3000")
            'http://localhost:3000'
            >>> QAExecutor._normalize_url("https://x.com")
            'https://x.com'
        """
        url = (raw or "").strip()
        if not url:
            return url
        if url.startswith(("http://", "https://", "file://", "data:", "about:")):
            return url
        if url.startswith("//"):
            return "https:" + url
        lower = url.lower()
        if lower.startswith(QAExecutor._LOCAL_HOST_PREFIXES) or QAExecutor._IPV4_RE.match(lower):
            return "http://" + url
        return "https://" + url

    @staticmethod
    def _normalize_step(step: dict):
        """
        Auto-correct common LLM mistakes in DSL steps.
        - press: when a key name is in target and value is empty, swap them.
        - navigate: when value is empty and target holds a URL, swap them.
        """
        action = step.get("action", "").lower()
        target = str(step.get("target", "")).strip()
        value = str(step.get("value", "")).strip()

        if action == "press" and not value and target.lower() in QAExecutor.KNOWN_KEYS:
            step["value"] = target
            step["target"] = ""
            log.debug("[normalize] press: target '%s' -> moved to value", target)

        # Common LLM mistake on navigate: putting the URL in target.
        # Even without a scheme, swap when it looks like a URL (e.g. 'foo.com', 'localhost:3000').
        if action == "navigate" and not value and target:
            host_part = target.split("/", 1)[0].split("?", 1)[0]
            looks_url = (
                target.startswith(("http://", "https://", "//"))
                or "." in host_part
                or host_part.startswith("localhost")
            )
            if looks_url:
                step["value"] = target
                step["target"] = ""
                log.debug("[normalize] navigate: target -> moved to value")

    def _resolve_upload_path(self, raw_path) -> str:
        """Resolve upload.value into a real file path under the artifacts root."""
        value = str(raw_path or "").strip()
        if not value:
            raise ValueError("upload.value is empty")

        allowed_root = os.path.abspath(self.config.artifacts_dir)
        candidates: list[str] = []
        if os.path.isabs(value):
            candidates.append(os.path.abspath(value))
        else:
            candidates.append(os.path.abspath(os.path.join(allowed_root, value)))
            candidates.append(os.path.abspath(os.path.join(allowed_root, os.path.basename(value))))

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if os.path.commonpath([allowed_root, candidate]) != allowed_root:
                continue
            if os.path.isfile(candidate):
                return candidate

        raise FileNotFoundError(
            f"upload file not found or outside allowed root: {value!r} "
            f"(allowed root: {allowed_root})"
        )

    @staticmethod
    def _normalize_mock_body(value) -> str:
        """Normalize mock_data.value into an application/json body string."""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)

        raw = str(value or "").strip()
        if not raw:
            raise ValueError("mock_data.value is empty")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return json.dumps(parsed, ensure_ascii=False)

    @staticmethod
    def _install_mock_route(
        page: Page,
        url_pattern: str,
        *,
        status: int | None = None,
        body: str | None = None,
        times: int = 1,
    ) -> None:
        """Install an API mock route.

        Args:
            page: Playwright Page.
            url_pattern: Glob or regex URL pattern.
            status: Response status code (for mock_status).
            body: Response JSON body string (for mock_data).
            times: Number of route matches to intercept. **Default 1** — to
                prevent cross-step global pollution. If the step dict carries a
                ``"times"`` key (separate from step.value), the caller can pass
                it through to mock polling / retry scenarios.
        """
        pattern = str(url_pattern or "").strip()
        if not pattern:
            raise ValueError("mock_* action requires target (URL pattern)")
        QAExecutor._enforce_mock_scope(pattern)

        def _handler(route):
            fulfill_args = {"status": status or 200}
            if body is not None:
                fulfill_args["body"] = body
                fulfill_args["content_type"] = "application/json"
            route.fulfill(**fulfill_args)

        page.route(pattern, _handler, times=max(1, int(times)))

    @staticmethod
    def _enforce_mock_scope(pattern: str) -> None:
        """Prevent overly broad or blocked-host mock routes.

        Playwright route mocking only affects the browser context, but an overly
        broad pattern can hide real failures and create false positives. The
        guard is opt-out via MOCK_OVERRIDE=1 for explicit operator actions.
        """
        if os.getenv("MOCK_OVERRIDE", "").strip() == "1":
            log.warning("[MockGuard] MOCK_OVERRIDE=1 — bypassing mock scope guard: %s", pattern)
            return

        normalized = pattern.strip().lower()
        target_host = urlparse(os.getenv("TARGET_URL", "")).hostname or ""
        blocked_hosts = {
            h.strip().lower()
            for h in os.getenv("MOCK_BLOCKED_HOSTS", "").split(",")
            if h.strip()
        }
        if target_host and target_host.lower() in blocked_hosts:
            blocked_hosts.add(target_host.lower())

        broad_patterns = {"*", "**", "/*", "/**", "**/*", "**/**"}
        is_broad = normalized in broad_patterns
        if is_broad and (target_host or blocked_hosts):
            raise ValueError(
                "mock_* target is too broad and risks false positives: "
                f"{pattern!r}. Set MOCK_OVERRIDE=1 to explicitly bypass."
            )

        for host in blocked_hosts:
            if host and host in normalized:
                raise ValueError(
                    "mock_* target matches a blocked host: "
                    f"host={host!r}, pattern={pattern!r}. "
                    "Set MOCK_OVERRIDE=1 to explicitly bypass."
                )

    def _execute_mock_step(
        self, page: Page, step: dict, artifacts: str
    ) -> StepResult:
        """Execute a mock_status / mock_data step.

        The input is a URL pattern, not a DOM element, so LocalHealer and
        fallback_targets DOM matching do not apply. Instead, supports a
        two-stage healing:

        1. If ``fallback_targets`` carries alternative URL pattern strings, try
           them in order.
        2. If all of the above fail, do Dify LLM healing (the healer prompt in
           the YAML has a mock_*-specific guide — that branch is activated).

        If ``step["times"]`` is an integer, it controls the mock route match
        count (default 1).
        """
        action = step["action"]
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        original_target = str(step.get("target", ""))

        try:
            self._apply_mock_route(page, step)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] %s -> PASS", step_id, action)
            return StepResult(
                step_id, action, original_target, str(step.get("value", "")), desc,
                "PASS", screenshot_path=ss,
            )
        except ValueError as e:
            log.warning("[Step %s] mock install failed: %s — trying fallback", step_id, e)

        # Stage 1: fallback_targets (alternative URL patterns).
        for fb_target in step.get("fallback_targets", []) or []:
            try:
                fb_step = {**step, "target": str(fb_target)}
                self._apply_mock_route(page, fb_step)
                step["target"] = str(fb_target)  # reflect into healed.json
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] mock fallback pattern recovery: %s", step_id, fb_target)
                return StepResult(
                    step_id, action, str(fb_target), str(step.get("value", "")), desc,
                    "HEALED", heal_stage="fallback", screenshot_path=ss,
                )
            except ValueError:
                continue

        # Stage 2: Dify LLM healing (URL pattern / value correction).
        try:
            new_target_info = self.dify.request_healing(
                error_msg=f"mock install failed: {original_target}",
                dom_snapshot="",  # mock_* is DOM-agnostic — pass empty context
                failed_step=step,
                strategy_trace=[a.to_dict() for a in self._latest_strategy_trace],
            )
        except DifyConnectionError as e:
            log.error("[Step %s] Dify heal communication failed: %s", step_id, e)
            new_target_info = None

        if new_target_info:
            step.update(new_target_info)
            try:
                self._apply_mock_route(page, step)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] mock LLM heal succeeded: %s", step_id, step.get("target"))
                return StepResult(
                    step_id, action, str(step.get("target", "")),
                    str(step.get("value", "")), desc,
                    "HEALED", heal_stage="dify", screenshot_path=ss,
                )
            except ValueError as e:
                log.error("[Step %s] mock still failed after LLM heal: %s", step_id, e)

        ss = self._screenshot(page, artifacts, step_id, "fail")
        return StepResult(
            step_id, action, original_target, str(step.get("value", "")), desc,
            "FAIL", screenshot_path=ss,
        )

    def _apply_mock_route(self, page: Page, step: dict) -> None:
        """Convert the step dict's action/target/value/times into _install_mock_route."""
        action = step["action"]
        pattern = str(step.get("target", ""))
        times = int(step.get("times", 1))
        if action == "mock_status":
            status_code = int(str(step.get("value", "")).strip())
            self._install_mock_route(page, pattern, status=status_code, times=times)
        else:  # mock_data
            body = self._normalize_mock_body(step.get("value"))
            self._install_mock_route(page, pattern, body=body, times=times)

    # ─────────────────────────────────────────────────────────────────
    # reset_state (T-B / P0.3-A)
    # ─────────────────────────────────────────────────────────────────

    def _execute_reset_state(
        self, page: Page, step: dict, artifacts: str,
    ) -> StepResult:
        """reset_state action — clear client-side state mid-scenario.

        DSL forms:
          {"action": "reset_state", "target": "", "value": "cookie"}     # cookies only
          {"action": "reset_state", "target": "", "value": "storage"}    # local + session
          {"action": "reset_state", "target": "", "value": "indexeddb"}  # IDB
          {"action": "reset_state", "target": "", "value": "all"}        # all of the above

        The value whitelist is kept in sync with `__main__._RESET_STATE_VALID_VALUES`.
        Uses only BrowserContext / Page level APIs — self-contained without any
        backend hook.
        """
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        scope = str(step.get("value", "")).strip().lower()

        try:
            if scope in ("cookie", "all"):
                page.context.clear_cookies()
                log.info("[Step %s] reset_state cookie -> cleared", step_id)

            if scope == "all":
                # docs/PLAN_PRODUCTION_READINESS.md §"T-B Day 2" — "all" includes
                # cookie + storage + indexeddb + permissions reset. Resets granted
                # permissions like geolocation/notifications/clipboard.
                try:
                    page.context.clear_permissions()
                    log.info("[Step %s] reset_state permissions -> cleared", step_id)
                except Exception as e:  # noqa: BLE001
                    # Unsupported on some Playwright versions / contexts — soft fail.
                    log.warning(
                        "[Step %s] reset_state permissions unsupported (skip): %s",
                        step_id, e,
                    )

            if scope in ("storage", "all"):
                # localStorage / sessionStorage can throw SecurityError on
                # origin-less pages like about:blank, so handle inside try.
                page.evaluate(
                    """() => {
                        try { localStorage.clear(); } catch (e) { /* no-op */ }
                        try { sessionStorage.clear(); } catch (e) { /* no-op */ }
                    }"""
                )
                log.info("[Step %s] reset_state storage -> cleared", step_id)

            if scope in ("indexeddb", "all"):
                page.evaluate(
                    """async () => {
                        if (!('indexedDB' in window) || !indexedDB.databases) return;
                        try {
                            const dbs = await indexedDB.databases();
                            await Promise.all(dbs.map(d => new Promise((res) => {
                                if (!d.name) return res();
                                const req = indexedDB.deleteDatabase(d.name);
                                req.onsuccess = req.onerror = req.onblocked = () => res();
                            })));
                        } catch (e) { /* no-op — when unsupported on Safari etc. */ }
                    }"""
                )
                log.info("[Step %s] reset_state indexeddb -> cleared", step_id)

        except Exception as e:  # noqa: BLE001
            log.error("[Step %s] reset_state %s failed: %s", step_id, scope, e)
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return StepResult(
                step_id, "reset_state", "", scope, desc,
                "FAIL", screenshot_path=ss,
            )

        ss = self._screenshot(page, artifacts, step_id, "pass")
        return StepResult(
            step_id, "reset_state", "", scope, desc,
            "PASS", screenshot_path=ss,
        )

    # ─────────────────────────────────────────────────────────────────
    # auth_login (T-D / P0.1)
    # ─────────────────────────────────────────────────────────────────

    def _execute_auth_login(
        self, page: Page, step: dict, artifacts: str,
    ) -> StepResult:
        """auth_login action — branches into form / totp / oauth modes.

        DSL forms:
          {"action": "auth_login", "target": "form", "value": "<credential_alias>"}
          {"action": "auth_login", "target": "totp", "value": "<credential_alias>"}
          {"action": "auth_login", "target": "form, email_field=#email, password_field=#pw, submit=#login",
           "value": "<credential_alias>"}

        Credentials are looked up from env vars `AUTH_CRED_<ALIAS>_USER` /
        `_PASS` / `_TOTP_SECRET`. See the zero_touch_qa.auth module docstring
        for the full spec.
        """
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        target_str = str(step.get("target", ""))
        alias = str(step.get("value", ""))

        opts = parse_auth_target(target_str)
        try:
            cred = resolve_credential(alias)
        except CredentialError as e:
            log.error("[Step %s] auth_login credential resolution failed: %s", step_id, e)
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        log.info(
            "[Step %s] auth_login mode=%s alias=%s user=%s pass=%s totp=%s",
            step_id, opts.mode, alias,
            mask_secret(cred.user, keep=2),
            mask_secret(cred.password, keep=0),
            "<set>" if cred.has_totp() else "<empty>",
        )

        if opts.mode == "form":
            return self._auth_login_form(page, step, opts, cred, artifacts)
        if opts.mode == "totp":
            return self._auth_login_totp(page, step, opts, cred, artifacts)
        if opts.mode == "oauth":
            # T-D Phase 5 — to be enabled after OAuth mock server integration.
            log.error(
                "[Step %s] auth_login oauth mode pending T-D Phase 5 (mock OAuth)",
                step_id,
            )
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        log.error("[Step %s] auth_login unknown mode=%r", step_id, opts.mode)
        ss = self._screenshot(page, artifacts, step_id, "fail")
        return StepResult(
            step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
            "FAIL", screenshot_path=ss,
        )

    def _auth_login_form(
        self, page: Page, step: dict, opts: AuthOptions, cred: Credential,
        artifacts: str,
    ) -> StepResult:
        """Form login — fill email + password fields and click submit."""
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        target_str = str(step.get("target", ""))
        alias = str(step.get("value", ""))

        # Capture sensitive input locators ahead of time to use in the mask list.
        # _find_auth_field may raise RuntimeError before fill, so initialize as
        # None and update inside the try.
        email_loc = pwd_loc = None
        try:
            email_loc = self._find_auth_field(
                page, opts.email_field, EMAIL_FIELD_CANDIDATES, "email/username",
            )
            pwd_loc = self._find_auth_field(
                page, opts.password_field, PASSWORD_FIELD_CANDIDATES, "password",
            )
            submit_loc = self._find_auth_field(
                page, opts.submit, SUBMIT_BUTTON_CANDIDATES, "submit",
            )

            email_loc.fill(cred.user, timeout=5000)
            pwd_loc.fill(cred.password, timeout=5000)
            submit_loc.click(timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception as e:
            log.error("[Step %s] auth_login form failed: %s", step_id, e)
            ss = self._screenshot_masked(
                page, artifacts, step_id, "fail",
                mask=[loc for loc in (email_loc, pwd_loc) if loc is not None],
            )
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        # P0.1 #3 — Mask the input fields so plaintext credentials do not remain
        # in the PASS screenshot. Locators detached by post-submit navigation
        # are no-ops inside Playwright.
        ss = self._screenshot_masked(
            page, artifacts, step_id, "pass", mask=[email_loc, pwd_loc],
        )
        log.info("[Step %s] auth_login form -> PASS", step_id)
        return StepResult(
            step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
            "PASS", screenshot_path=ss,
        )

    def _auth_login_totp(
        self, page: Page, step: dict, opts: AuthOptions, cred: Credential,
        artifacts: str,
    ) -> StepResult:
        """TOTP login — generate the 6-digit code with pyotp and fill it."""
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        target_str = str(step.get("target", ""))
        alias = str(step.get("value", ""))

        if not cred.has_totp():
            log.error(
                "[Step %s] auth_login totp failed — alias '%s' has no TOTP secret",
                step_id, alias,
            )
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        otp_loc = None
        try:
            code = generate_totp_code(cred.totp_secret)
            otp_loc = self._find_auth_field(
                page, opts.totp_field, TOTP_FIELD_CANDIDATES, "totp",
            )
            otp_loc.fill(code, timeout=5000)
            # submit — click a separate button if present, otherwise leave as-is
            # (assume an auto-submit form).
            submit_loc = self._try_find_auth_field(
                page, opts.submit, SUBMIT_BUTTON_CANDIDATES,
            )
            if submit_loc is not None:
                submit_loc.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception as e:
            log.error("[Step %s] auth_login totp failed: %s", step_id, e)
            ss = self._screenshot_masked(
                page, artifacts, step_id, "fail",
                mask=[loc for loc in (otp_loc,) if loc is not None],
            )
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        # P0.1 #3 — Mask so the TOTP code does not remain in the PASS screenshot.
        ss = self._screenshot_masked(
            page, artifacts, step_id, "pass", mask=[otp_loc],
        )
        log.info("[Step %s] auth_login totp -> PASS (code=******)", step_id)
        return StepResult(
            step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
            "PASS", screenshot_path=ss,
        )

    @staticmethod
    def _find_auth_field(
        page: Page, explicit: Optional[str], candidates: tuple, field_name: str,
    ) -> Locator:
        """If an explicit selector is provided use it, else try candidate selectors in order.

        Raises RuntimeError if there are zero matches. Returns ``.first`` of the
        first matching element.
        """
        if explicit:
            loc = page.locator(explicit)
            try:
                if loc.count() > 0:
                    return loc.first
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"auth_login {field_name} field 0 matches (explicit={explicit!r})")
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return loc.first
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(
            f"auth_login {field_name} field auto-detect failed — candidates: {list(candidates)}"
        )

    @staticmethod
    def _try_find_auth_field(
        page: Page, explicit: Optional[str], candidates: tuple,
    ) -> Optional[Locator]:
        """Optional variant of ``_find_auth_field`` — returns None instead of raising RuntimeError."""
        if explicit:
            try:
                loc = page.locator(explicit)
                if loc.count() > 0:
                    return loc.first
            except Exception:  # noqa: BLE001
                return None
            return None
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return loc.first
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _assert_locator_contains_value(locator: Locator, expected: str) -> None:
        """For verify-backward-compat, considers both text_content and input_value."""
        actual = ""
        try:
            actual = (locator.inner_text() or "").strip()
        except Exception:
            actual = ""
        if not actual:
            try:
                actual = (locator.text_content() or "").strip()
            except Exception:
                actual = ""
        if not actual:
            try:
                actual = (locator.input_value() or "").strip()
            except Exception:
                actual = ""
        if str(expected) not in actual:
            raise VerificationAssertionError(
                f"text/value mismatch: expected='{expected}', actual='{actual}'"
            )

    # ── A: per-action strategy chain (multi-strategy + post-condition) ──
    #
    # Motivation: forcing a single mapping (e.g. select_option(label=...)) creates a
    # class of failures even an LLM healer can't fix. Letting each action try multiple
    # mappings/forms and verify the result itself absorbs every deterministically
    # recoverable case before the healer is even called.
    #
    # Attempt results accumulate in ``self._latest_strategy_trace`` and are injected
    # as context on the Dify healer call (preserves signals like "even after changing
    # only the selector the timeout was the same").

    @staticmethod
    def _normalize_check_state(value) -> bool:
        s = str(value or "").strip().lower()
        if s in ("false", "off", "no", "0", "uncheck", "unchecked"):
            return False
        # Empty value defaults to check.
        return True

    def _do_select(self, locator: Locator, value: str) -> None:
        """Multi-strategy select. Order: positional -> value= -> label=. Post-check: actually selected."""
        trace: list[_StrategyAttempt] = []
        last_err: Exception | None = None

        def post_check():
            try:
                actual = locator.evaluate("el => el.value")
            except Exception:
                actual = None
            try:
                sel_text = locator.evaluate(
                    "el => el.options && el.options[el.selectedIndex] "
                    "&& el.options[el.selectedIndex].text"
                ) or ""
            except Exception:
                sel_text = ""
            if value and value != actual and value not in str(sel_text):
                raise RuntimeError(
                    f"select post-check failed: expected={value!r}, "
                    f"actual_value={actual!r}, label={sel_text!r}"
                )

        strategies = [
            ("positional", lambda: locator.select_option(value, timeout=5000)),
            ("value=",     lambda: locator.select_option(value=value, timeout=5000)),
            ("label=",     lambda: locator.select_option(label=value, timeout=5000)),
        ]
        for name, fn in strategies:
            try:
                fn()
                post_check()
                trace.append(_StrategyAttempt(name, ""))
                self._latest_strategy_trace = trace
                return
            except Exception as e:
                trace.append(_StrategyAttempt(name, str(e)[:200]))
                last_err = e

        self._latest_strategy_trace = trace
        raise last_err if last_err else RuntimeError("select: all strategies failed")

    def _do_check(self, locator: Locator, value: str) -> None:
        """Multi-strategy check. Order: native -> click toggle -> JS force-set. Post-check: is_checked()."""
        desired = self._normalize_check_state(value)
        trace: list[_StrategyAttempt] = []
        last_err: Exception | None = None

        def native():
            if desired:
                locator.check()
            else:
                locator.uncheck()

        def click_to_match():
            if locator.is_checked() != desired:
                locator.click()

        def force_set():
            locator.evaluate(
                "(el, v) => { el.checked = v; "
                "el.dispatchEvent(new Event('change', {bubbles:true})); }",
                desired,
            )

        strategies = [
            ("native",    native),
            ("click",     click_to_match),
            ("force-set", force_set),
        ]
        for name, fn in strategies:
            try:
                fn()
                actual = locator.is_checked()
                if actual != desired:
                    raise RuntimeError(
                        f"check post-check: actual={actual} != desired={desired}"
                    )
                trace.append(_StrategyAttempt(name, ""))
                self._latest_strategy_trace = trace
                return
            except Exception as e:
                trace.append(_StrategyAttempt(name, str(e)[:200]))
                last_err = e

        self._latest_strategy_trace = trace
        raise last_err if last_err else RuntimeError("check: all strategies failed")

    def _upload_path_candidates(self, value: str) -> list[tuple[str, str]]:
        """Expand upload.value into multiple path variants. List of (strategy name, absolute path).

        The last candidate is always the ``upload_sample.txt`` default dummy —
        ensures deterministic PASS even when the LLM emits a placeholder like
        ``test.txt``.
        """
        artifacts_root = os.path.abspath(self.config.artifacts_dir)
        candidates: list[tuple[str, str]] = []
        if os.path.isabs(value):
            candidates.append(("absolute", os.path.abspath(value)))
        else:
            candidates.append((
                "artifacts/value",
                os.path.abspath(os.path.join(artifacts_root, value)),
            ))
            candidates.append((
                "artifacts/basename",
                os.path.abspath(os.path.join(artifacts_root, os.path.basename(value))),
            ))
            scripts_home = os.environ.get("SCRIPTS_HOME") or ""
            if scripts_home:
                candidates.append((
                    "scripts_home/test/fixtures",
                    os.path.abspath(
                        os.path.join(scripts_home, "test", "fixtures", value)
                    ),
                ))

        # Default dummy fallback — the pipeline pre-creates ``upload_sample.txt``
        # inside artifacts, so it always exists. Deterministically absorbs LLM
        # placeholder values too.
        candidates.append((
            "artifacts/default-sample",
            os.path.abspath(os.path.join(artifacts_root, "upload_sample.txt")),
        ))

        # dedup, preserve order
        seen: set[str] = set()
        uniq: list[tuple[str, str]] = []
        for name, p in candidates:
            if p not in seen:
                seen.add(p)
                uniq.append((name, p))
        return uniq

    def _do_upload(self, locator: Locator, value: str) -> None:
        """Multi-strategy upload. Iterates candidate paths + post-check (input.value endswith basename)."""
        if not value:
            raise ValueError("upload.value is empty")

        trace: list[_StrategyAttempt] = []
        last_err: Exception | None = None

        for name, path in self._upload_path_candidates(value):
            if not os.path.exists(path):
                trace.append(_StrategyAttempt(name, f"not found: {path}"))
                continue
            # Security guard: only allow paths under artifacts root or SCRIPTS_HOME.
            allowed_roots = [os.path.abspath(self.config.artifacts_dir)]
            sh = os.environ.get("SCRIPTS_HOME") or ""
            if sh:
                allowed_roots.append(os.path.abspath(sh))
            if not any(path.startswith(root + os.sep) or path == root for root in allowed_roots):
                trace.append(_StrategyAttempt(
                    name, f"security guard: outside allowed roots — {path}"
                ))
                continue
            try:
                locator.set_input_files(path)
                actual = (locator.input_value() or "")
                expected_basename = os.path.basename(path)
                if not actual.endswith(expected_basename):
                    raise RuntimeError(
                        f"upload post-check: input.value={actual!r}, "
                        f"expected basename={expected_basename!r}"
                    )
                trace.append(_StrategyAttempt(name, ""))
                self._latest_strategy_trace = trace
                return
            except Exception as e:
                trace.append(_StrategyAttempt(name, str(e)[:200]))
                last_err = e

        self._latest_strategy_trace = trace
        raise last_err if last_err else FileNotFoundError(
            f"all upload candidate paths unusable: {value!r}"
        )

    def _do_fill(self, locator: Locator, value: str) -> None:
        """Multi-strategy fill. Order: clear+fill -> type -> JS evaluate. Post-check: input_value()."""
        trace: list[_StrategyAttempt] = []
        last_err: Exception | None = None

        def clear_then_fill():
            locator.fill("")
            locator.fill(value)

        def type_with_delay():
            locator.fill("")
            locator.type(value, delay=20)

        def js_set():
            locator.evaluate(
                "(el, v) => { el.value = v; "
                "el.dispatchEvent(new Event('input', {bubbles:true})); "
                "el.dispatchEvent(new Event('change', {bubbles:true})); }",
                value,
            )

        strategies = [
            ("clear+fill", clear_then_fill),
            ("type",       type_with_delay),
            ("js-set",     js_set),
        ]
        for name, fn in strategies:
            try:
                fn()
                actual = locator.input_value() or ""
                if actual != value:
                    raise RuntimeError(
                        f"fill post-check: actual={actual!r} != expected={value!r}"
                    )
                trace.append(_StrategyAttempt(name, ""))
                self._latest_strategy_trace = trace
                return
            except Exception as e:
                trace.append(_StrategyAttempt(name, str(e)[:200]))
                last_err = e

        self._latest_strategy_trace = trace
        raise last_err if last_err else RuntimeError("fill: all strategies failed")

    # ── execute the 14 DSL actions ──
    def _perform_action(
        self, page: Page, locator: Locator, step: dict, resolver: LocatorResolver
    ):
        """Perform the 14 DSL actions as actual Playwright operations.

        Args:
            page: Playwright Page (used by verify).
            locator: Playwright Locator for the target element.
            step: DSL step dict. References ``action`` and ``value`` keys.
            resolver: LocatorResolver used to resolve extra targets like drag destinations.

        Raises:
            ValueError: when the action is unsupported.
            VerificationAssertionError: when a verify action's condition does not match.
        """
        action = step["action"].lower()
        value = step.get("value", "")
        # A: Reset strategy trace every step. The healer only sees the last step's trace.
        self._latest_strategy_trace = []

        if action == "click":
            # 1) Explicitly scroll into the viewport (best-effort, ignore failures).
            #    Playwright auto-scrolls on click, but on pages with frequent
            #    dynamic reflow (e.g. Yahoo ads) it can fail to acquire stability
            #    and time out — this avoids that.
            # 2) timeout 5s -> 10s — accommodates pages where ads/lazy image loads
            #    delay stability.
            try:
                locator.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            # P-3. Pre-log the click target's href — when a stretched-box-style
            # invisible overlay is clicked and triggers navigation, we can trace
            # which link it was after the fact.
            try:
                target_href = locator.get_attribute("href", timeout=1000)
                target_text = (locator.text_content(timeout=1000) or "").strip()[:60]
                if target_href or target_text:
                    log.info("[Click] href=%r text=%r", target_href, target_text)
            except Exception:
                pass
            # E-3: A "first result/link/item" click MUST produce a navigation effect.
            # Blocks the false positive (e.g. Yahoo home's stretched-box overlay)
            # where the click succeeds but no navigation happens. If there is no
            # effect, raise RuntimeError -> proceed through the fallback chain.
            desc = str(step.get("description", ""))
            need_nav = QAExecutor._matches_first_result_intent(desc)
            before_url = page.url if need_nav else ""
            before_pages_count = len(page.context.pages) if need_nav else 0
            try:
                locator.click(timeout=10000)
            except Exception as click_err:
                # T-H (G) — last resort for the case where Playwright rejects click
                # actionability (height:0 / outside viewport / hidden). Try JS
                # dispatchEvent('click') only if the element is an anchor/button.
                # Handles sites like ktds.com whose GNB link has computed style
                # height:0 / line-height:0 so normal click forever rejects actionability.
                if not _is_safe_for_js_click(locator):
                    raise
                msg = str(click_err)
                if not any(
                    s in msg for s in (
                        "not visible", "outside of the viewport",
                        "intercepts pointer events", "Element is not stable",
                    )
                ):
                    raise
                log.warning(
                    "[Click] Playwright click rejected (%s) → trying JS dispatch click fallback",
                    msg.split("\n", 1)[0][:120],
                )
                locator.evaluate("el => el.click()")
            if need_nav and not QAExecutor._wait_for_navigation_effect(
                page, before_url, before_pages_count
            ):
                raise RuntimeError(
                    f"no navigation after 'first result' click — "
                    f"URL unchanged ({before_url}), no valid new tab. "
                    f"Link may be obscured by an overlay or blocked by bot detection."
                )
        elif action == "fill":
            # A: multi-strategy + post-condition (clear+fill / type / js-set).
            self._do_fill(locator, str(value))
        elif action == "press":
            # M+N. post-press check — when in a press Enter + 'search' intent context,
            # one of these must hold for a real submit: (a) URL changed, (b) a new tab/window
            # opened and its URL is a valid content page (not chrome-error/about:blank).
            # If neither holds, raise to drive fallback/alternatives/B heuristics.
            #
            # We added the chrome-error filter because sites like Yahoo, when they detect a bot
            # and block the form submit, open the new tab on chrome-error. Mistaking that for
            # "submit success" would produce a false-positive PASS on the original homepage.
            before_url = page.url
            context = page.context
            before_pages = len(context.pages)
            locator.press(str(value))
            if str(value).lower() in ("enter", "return"):
                desc = str(step.get("description", ""))
                # Anti-flake heuristic for search forms — prevents the case where an
                # external search site blocks bots and opens a chrome-error new tab,
                # which would otherwise let the next verify step PASS falsely.
                # However, fixture environments (localhost / file://) typically just do a
                # DOM update (e.g. changing #echo text), so we exclude them from the strict check.
                # The following verify step covers real behavior validation, so no need to block here.
                is_local_fixture = before_url.startswith(
                    ("http://localhost", "http://127.0.0.1", "file://")
                )
                if re.search(r"검색|search", desc, re.IGNORECASE) and not is_local_fixture:
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        if page.url != before_url:
                            break
                        if len(context.pages) > before_pages:
                            break
                        page.wait_for_timeout(100)
                    url_changed = page.url != before_url
                    new_pages = context.pages[before_pages:]
                    valid_new_tab = any(
                        not (pg.url or "").startswith(
                            ("chrome-error://", "about:blank", "data:text/html")
                        )
                        for pg in new_pages
                    )
                    if not url_changed and not valid_new_tab:
                        new_tab_urls = [pg.url for pg in new_pages] or ["(none)"]
                        raise RuntimeError(
                            f"search submit failed after press Enter — "
                            f"URL unchanged ({before_url}) + no valid new tab. "
                            f"new tab URLs: {new_tab_urls} "
                            f"(chrome-error/about:blank treated as bot-blocked)"
                        )
        elif action == "upload":
            # A: multi-strategy on the candidate path + post-condition (input.value endswith basename).
            self._do_upload(locator, str(value))
        elif action == "drag":
            target_locator = resolver.resolve(value)
            if not target_locator:
                raise RuntimeError(f"failed to resolve drag destination: {value!r}")
            try:
                locator.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                target_locator.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            locator.drag_to(target_locator, timeout=10000)
        elif action == "scroll":
            locator.scroll_into_view_if_needed(timeout=5000)
        elif action == "select":
            # A: multi-strategy + post-condition. Order: positional → value= → label=.
            self._do_select(locator, str(value))
        elif action == "check":
            # A: multi-strategy + post-condition. native → click → JS force-set.
            self._do_check(locator, str(value))
        elif action == "hover":
            locator.hover()
        elif action == "verify":
            # E-2: a "verify search results (list/exists/visible)" intent verify must run
            # on a URL that is **actually the search-results page**. Prevents a false PASS where
            # bot-blocking keeps us on the homepage and an arbitrary main/article is visible.
            desc = str(step.get("description", ""))
            if QAExecutor._matches_search_results_intent(desc):
                current_url = page.url or ""
                if not QAExecutor._SEARCH_RESULT_URL_RE.search(current_url):
                    raise VerificationAssertionError(
                        f"search-results verify failed — current URL is not a search-results page: "
                        f"{current_url} "
                        f"(verify whether search submission actually happened in the previous step)"
                    )
            condition = str(step.get("condition", "")).strip().lower()
            try:
                if condition in ("", "visible"):
                    if not value:
                        expect(locator).to_be_visible()
                    else:
                        self._assert_locator_contains_value(locator, str(value))
                elif condition == "hidden":
                    expect(locator).not_to_be_visible()
                elif condition == "disabled":
                    expect(locator).to_be_disabled()
                elif condition == "enabled":
                    expect(locator).to_be_enabled()
                elif condition == "checked":
                    expect(locator).to_be_checked()
                elif condition == "value":
                    expect(locator).to_have_value(str(value))
                elif condition in ("text", "contains_text", "contains"):
                    expect(locator).to_contain_text(str(value))
                else:
                    raise ValueError(
                        f"unsupported verify.condition: {condition!r} "
                        f"(allowed: visible, hidden, disabled, enabled, checked, value, text)"
                    )
            except AssertionError as e:
                raise VerificationAssertionError(str(e)) from e
        else:
            raise ValueError(
                f"unsupported DSL action: '{action}'. "
                "allowed: navigate, click, fill, press, select, check, hover, wait, "
                "verify, upload, drag, scroll, mock_status, mock_data"
            )

    # ── Visibility Healer (T-H) ──
    # Case where codegen drops the hover from a hover-then-click sequence and we try
    # to click while the element is hidden. Find a hoverable ancestor (aria-haspopup /
    # role=menu / nav / dropdown class / :hover CSS rule), hover it, and recheck.
    # Called only right before the first attempt — zero impact on normal (already visible) cases.

    def _heal_visibility(
        self, page: Page, locator: Locator, step_id,
    ) -> Optional[Locator]:
        """If the element is hidden, try to make it visible in 5 stages.

        Order (each stage short-circuits as soon as the element is visible):
          (1) `scroll_into_view_if_needed` — for sites triggered by Intersection Observer.
          (2) cascade ancestor hover — accumulate hover on the hoverable candidates
              extracted by `_VISIBILITY_HEALER_JS` from outermost → innermost. Multi-level
              menus (e.g. About > Company history > ~2013) need the outer hover first
              so the next level becomes visible.
          (3) page-level activator probe — `<header>`/`<nav>`/`<main>`/`<body>` hover.
              Handles sites where global hover events activate the menu.
          (4) size-aware poll — wait up to 2s for bounding_box.height/width > 0.
              Handles sites where font/CSS async loading expands the menu late.
          (5) sibling swap — replace the match with `filter(visible=True).first`.

        Total budget ~6s across stages. For already-visible elements, only check (0) runs.

        Returns:
            A Locator if we found a visible sibling match; otherwise None
            (meaning: it is OK to use the original locator as-is).
        """
        try:
            if locator.is_visible():
                return None
        except Exception:
            return None  # invalid-locator case is handled by the next healer

        # ── (1) D — scroll_into_view ─────────────────────────────────────
        # Playwright brings the element into the viewport. Effective for lazy menus
        # backed by Intersection Observer. Works even on 0-size elements as long as a
        # position is known. Failures are silent (move to the next stage).
        try:
            locator.scroll_into_view_if_needed(timeout=1500)
            page.wait_for_timeout(150)
            if locator.is_visible():
                log.info("[Step %s] visibility-healer recovered via scroll_into_view", step_id)
                return None
        except Exception:
            pass

        # ── (2) cascade ancestor hover (outermost → innermost) ──────────
        # `_VISIBILITY_HEALER_JS` walks from leaf upward → candidates[0] is closest
        # to the leaf and [-1] is the outermost. Multi-level hover menus (e.g. ktds.com's
        # About > Company history > ~2013) require hover from outermost first, so each
        # stage's :hover cascades and the next trigger becomes visible.
        # A single ancestor hover only opens 1 level and fails at 2 levels or more.
        # Playwright hover() moves the mouse to the element center — if the next hover
        # is a descendant, the ancestor's :hover is automatically preserved (browser behavior).
        try:
            candidates = locator.evaluate(_VISIBILITY_HEALER_JS)
        except Exception as e:  # noqa: BLE001
            log.debug("[Step %s] visibility-healer evaluate failed: %s", step_id, e)
            candidates = []

        chain = list(reversed(candidates))[:5]  # cascade up to 5 levels
        hovered_path: list[str] = []
        for cand in chain:
            sel = cand.get("path") or ""
            reason = cand.get("reason") or "unknown"
            if not sel:
                continue
            try:
                ancestor = page.locator(sel).first
                ancestor.hover(timeout=1500)
                page.wait_for_timeout(150)  # menu transition
                hovered_path.append(f"{sel}({reason})")
                if locator.is_visible():
                    log.info(
                        "[Step %s] visibility-healer recovered — cascade hover %s",
                        step_id, " > ".join(hovered_path),
                    )
                    return None
            except Exception:  # noqa: BLE001
                continue

        # ── (3) E — page-level activator probe ──────────────────────────
        # Fire mousemove/hover events at the page level to simulate the start of
        # user interaction. On sites like ktds.com where the GNB lazy-expands, just
        # hovering the header region can be enough to open the menu.
        for activator_sel in ("header", "nav", "main", "body"):
            try:
                target = page.locator(activator_sel).first
                if target.count() == 0:
                    continue
                target.hover(timeout=1000)
                page.wait_for_timeout(200)
                if locator.is_visible():
                    log.info(
                        "[Step %s] visibility-healer recovered — page-level hover (%s)",
                        step_id, activator_sel,
                    )
                    return None
            except Exception:  # noqa: BLE001
                continue

        # ── (4) F — size-aware poll ─────────────────────────────────────
        # Wait up to 2s for bounding_box.height/width > 0. Handles the case where
        # the menu expands gradually right after page load (CSS/JS animation transitions).
        try:
            for _ in range(10):  # 200ms x 10 = 2s
                page.wait_for_timeout(200)
                if locator.is_visible():
                    log.info("[Step %s] visibility-healer recovered — size poll", step_id)
                    return None
        except Exception:  # noqa: BLE001
            pass

        # ── (5) C — sibling-match swap ────────────────────────────────────
        sibling = self._find_visible_sibling(locator, step_id)
        if sibling is not None:
            return sibling

        log.debug(
            "[Step %s] visibility-healer — all strategies ineffective (scroll/ancestor/page-hover/size-poll/sibling)",
            step_id,
        )
        return None

    @staticmethod
    def _find_visible_sibling(locator: Locator, step_id) -> Optional[Locator]:
        """When a Locator has multiple matches and ``.first`` is hidden, swap to a visible sibling.

        Uses Playwright 1.36+'s ``filter(visible=True)``. Filters only the visible
        elements from ``.first``'s parent scope (= original match set) and returns the first.
        """
        try:
            visible = locator.filter(visible=True)
            if visible.count() > 0:
                first = visible.first
                if first.is_visible():
                    log.info(
                        "[Step %s] visibility-healer — sibling-match swap (filter(visible=True).first)",
                        step_id,
                    )
                    return first
        except Exception:  # noqa: BLE001
            pass
        return None

    # ── Screenshots ──
    @staticmethod
    def _screenshot(page: Page, artifacts: str, step_id, suffix: str) -> str:
        """Save a screenshot after step execution and return the file path."""
        path = os.path.join(artifacts, f"step_{step_id}_{suffix}.png")
        page.screenshot(path=path)
        return path

    @staticmethod
    def _screenshot_masked(
        page: Page, artifacts: str, step_id, suffix: str,
        mask: Optional[list] = None,
    ) -> str:
        """Masking variant of ``_screenshot`` — black-boxes the specified locator positions.

        T-D (P0.1 #3) — prevents elements that leave plaintext on the screen
        (auth_login email/password/TOTP inputs, etc.) from leaking through PNG
        captures. ``mask`` is a list of Locator or None. detached / 0-count locators
        are no-op'd internally by Playwright, so it is safe to pass them through
        even on a page that has navigated after submit.
        """
        path = os.path.join(artifacts, f"step_{step_id}_{suffix}.png")
        try:
            page.screenshot(path=path, mask=mask or [])
        except TypeError:
            # Some older Playwright versions do not support the mask argument — for
            # safety, do not save the screenshot at all (avoid leaking credentials).
            log.warning(
                "[Step %s] mask unsupported in this Playwright — skipping auth_login screenshot (security)",
                step_id,
            )
            return ""
        return path

    @staticmethod
    def _safe_screenshot(page: Page, path: str):
        """Save a screenshot; suppress exceptions on failure."""
        try:
            page.screenshot(path=path)
        except Exception:
            pass
