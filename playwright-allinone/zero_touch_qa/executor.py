import json
import os
import random
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, parse_qs


# 사이트가 인증 필요 페이지에 대해 native alert 대신 URL 쿼리 파라미터로 메시지를
# 전달하는 한국형 엔터프라이즈 패턴. `errorMsg` / `error_msg` / `msg` 가 있으면
# decode 해 사용자에게 보여줄 텍스트로 반환. 없으면 None.
_REDIRECT_MSG_KEYS = ("errorMsg", "error_msg", "msg")


def _extract_redirect_msg(url: str) -> "str | None":
    if not url:
        return None
    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=False)
    except Exception:  # noqa: BLE001
        return None
    for key in _REDIRECT_MSG_KEYS:
        vals = qs.get(key)
        if vals and vals[0]:
            return f"[redirect:{key}] {vals[0]}"
    return None

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


# Healer 가 action 자체를 변경하는 것은 false-PASS 위험이 크기 때문에, 의미적으로
# 등가인 좁은 전이 집합만 허용한다. 그룹 간 전이 (예: navigate ↔ verify, drag → click)
# 는 의도된 검증 자체를 무력화하므로 절대 통과시키지 않는다. dify-chatflow.yaml 의
# Healer system prompt 와 1:1 동기화돼 있다.
_HEAL_ACTION_TRANSITIONS = frozenset({
    ("select", "fill"), ("fill", "select"),
    ("check", "click"), ("click", "check"),
    ("click", "press"), ("press", "click"),
    ("upload", "click"), ("click", "upload"),
})


def _is_allowed_action_transition(old_action: str, new_action: str) -> bool:
    """Healer 가 제안한 action 변경이 화이트리스트 전이인지 검사한다."""
    if not isinstance(old_action, str) or not isinstance(new_action, str):
        return False
    if old_action == new_action:
        return True
    return (old_action.lower(), new_action.lower()) in _HEAL_ACTION_TRANSITIONS


class VerificationAssertionError(AssertionError):
    """요소 탐색은 성공했지만 verify 조건이 맞지 않을 때 사용한다."""


@dataclass
class _StrategyAttempt:
    """단일 전략 시도 결과. ``error`` 가 비어있으면 그 전략으로 PASS."""
    name: str
    error: str = ""

    def to_dict(self) -> dict:
        return {"strategy": self.name, "error": self.error or "ok"}


@dataclass
class StepResult:
    """단일 DSL 스텝의 실행 결과를 담는 데이터클래스.

    Attributes:
        step_id: 시나리오 내 스텝 번호 또는 식별자.
        action: 수행된 DSL 액션 이름 (click, fill, navigate 등).
        target: 실제로 사용된 로케이터 문자열.
        value: 액션에 전달된 값 (입력 텍스트, URL, 키 이름 등).
        description: 스텝에 대한 사람이 읽을 수 있는 설명.
        status: 실행 결과. ``"PASS"`` | ``"HEALED"`` | ``"FAIL"`` | ``"SKIP"``.
        heal_stage: 치유 성공 시 어느 단계에서 복구되었는지. ``"none"`` | ``"fallback"`` | ``"local"`` | ``"dify"``.
        timestamp: 스텝 실행 시각 (Unix epoch).
        screenshot_path: 스크린샷 파일 경로. 없으면 ``None``.
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
    # 스텝 실행 중 발생한 네이티브 dialog (alert/confirm/prompt/beforeunload) 의
    # message 본문. Playwright 가 자동 dismiss 해 스크린샷에는 절대 안 잡히므로
    # 텍스트만 보존해 리포트가 "여기서 alert 떴음" 을 명시. None 이면 발생 안 함.
    dialog_text: str | None = None


# Visibility Healer (T-H) JS — element 의 ancestor chain 에서 hoverable 후보 추출.
# 우선순위: aria-haspopup > aria-expanded=false > role=menu/menubar/listbox/tooltip/combobox >
#          tag=nav/details/summary > [data-state=closed] / [hidden] toggleable > :hover CSS rule.
# 각 후보에 대해 stable CSS path 를 함께 반환 (id 우선 → nth-of-type chain).
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

  // :hover CSS rule 의 trigger 인지 검사. selectorText 가 'A:hover B' 라면
  // trigger 는 A — node 가 A 와 matches 하면 hoverable.
  // 'ul#gnb > li:hover > .submenu' → trigger=`ul#gnb > li`.
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


# T-H (G) — JS dispatchEvent('click') 폴백 안전 가드.
# anchor/button/input/role=button/role=link/role=menuitem 만 허용. 일반 div 에
# JS click 발사하면 실 사이트의 listener 가 없어 false-positive PASS 위험.
def _is_safe_for_js_click(locator) -> bool:
    """element 가 anchor/button/clickable role 이면 JS click 안전. 그 외는 raise."""
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
    """현재 BrowserContext 의 storage_state 를 path 에 JSON 으로 덤프 (T-D / P0.1).

    실패 시 경고만 — 시나리오 실행 결과 자체에 영향 주지 않는다.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        context.storage_state(path=path)
        log.info("[Auth] storage_state 덤프 완료 — %s", path)
    except Exception as e:  # noqa: BLE001
        log.warning("[Auth] storage_state 덤프 실패 (%s): %s", path, e)


def _apply_fingerprint_env(context_kwargs: dict) -> None:
    """auth-profile fingerprint env override 를 ``context_kwargs`` 에 적용 (P4.1).

    설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.8 (D10).

    ``replay_proxy`` 가 시드 시점의 fingerprint (viewport / locale / timezone /
    color_scheme) 를 env 로 주입하면 본 함수가 기본값을 덮어쓴다. UA 는 의도적으로
    빠짐 — sec-ch-ua Client Hints 와의 어긋남을 방지하기 위해 임의 spoof 안 함.

    영향:
        - ``PLAYWRIGHT_VIEWPORT``      = ``"<W>x<H>"`` (예: ``1280x800``)
        - ``PLAYWRIGHT_LOCALE``        = locale 문자열 (예: ``ko-KR``)
        - ``PLAYWRIGHT_TIMEZONE``      = IANA timezone (예: ``Asia/Seoul``)
        - ``PLAYWRIGHT_COLOR_SCHEME``  = ``"light"`` / ``"dark"`` / ``"no-preference"``
    """
    viewport_env = os.environ.get("PLAYWRIGHT_VIEWPORT", "")
    if viewport_env and "x" in viewport_env:
        try:
            w_str, h_str = viewport_env.split("x", 1)
            context_kwargs["viewport"] = {"width": int(w_str), "height": int(h_str)}
        except (ValueError, IndexError):
            log.warning(
                "[Auth] PLAYWRIGHT_VIEWPORT 형식 오류 (무시) — %r", viewport_env,
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
    DSL 시나리오를 받아 실행하고, 3단계 하이브리드 자가 치유를 수행한다.

    치유 루프:
      1. fallback_targets 순회 (무비용)
      2. LocalHealer DOM 유사도 매칭
      3. DifyClient LLM 치유
    """

    def __init__(self, config: Config):
        self.config = config
        self.dify = DifyClient(config)
        # A: 직전 step 의 strategy chain 시도 기록. _perform_action 진입 시 reset.
        # Dify healer 호출 시 LLM 컨텍스트로 주입 → "selector 만 바꾸면 같은 timeout"
        # 같은 정보를 LLM 이 알 수 있게 한다.
        self._latest_strategy_trace: list[_StrategyAttempt] = []

    def execute(
        self,
        scenario: list[dict],
        headed: bool = True,
        storage_state_in: Optional[str] = None,
        storage_state_out: Optional[str] = None,
    ) -> list[StepResult]:
        """Playwright 브라우저를 실행하고 DSL 시나리오를 순차 실행한다.

        Args:
            scenario: DSL 스텝 dict 의 리스트.
            headed: True 면 브라우저 창을 표시, False 면 headless.
            storage_state_in: 미리 dump 된 storage_state JSON 경로 — 인증 후 세션
                을 새 컨텍스트에 복원한다 (T-D / P0.1). None 이면 env
                ``AUTH_STORAGE_STATE_IN``, 그것도 없으면 새 컨텍스트.
            storage_state_out: 시나리오 종료 후 현재 컨텍스트의 storage_state 를
                덤프할 경로. None 이면 env ``AUTH_STORAGE_STATE_OUT``, 그것도
                없으면 덤프 안 함.

        Returns:
            각 스텝의 실행 결과 ``StepResult`` 리스트. FAIL 발생 시 이후 스텝은 포함되지 않는다.
        """
        results: list[StepResult] = []
        artifacts = self.config.artifacts_dir
        os.makedirs(artifacts, exist_ok=True)

        # T-D / P0.1 — storage_state 경로 결정 (인자 우선, env fallback)
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
            # replay_proxy 가 시드 시점의 fingerprint 를 env 로 주입하면 여기서
            # context_kwargs 의 기본값을 덮어쓴다. UA 는 스푸핑하지 않는다 (D10).
            _apply_fingerprint_env(context_kwargs)
            if storage_state_in and os.path.isfile(storage_state_in):
                log.info("[Auth] storage_state 복원 — %s", storage_state_in)
                context_kwargs["storage_state"] = storage_state_in
            elif storage_state_in:
                log.warning(
                    "[Auth] storage_state_in 파일 없음 — 새 컨텍스트로 진행 (%s)",
                    storage_state_in,
                )
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            resolver = LocatorResolver(page)
            healer = LocalHealer(page, self.config.heal_threshold)

            # 네이티브 dialog 캡처 — 스텝 단위로 채우고 비우는 버퍼.
            # Playwright 는 dialog 핸들러가 없으면 자동 dismiss 하지만 그 전에
            # 스크린샷이 떠도 alert 는 OS chrome 레이어라 viewport 에 안 잡힘.
            # 따라서 텍스트만 보존해 리포트에서 표시 (운영자 인지 목적).
            dialog_buffer: list[str] = []
            def _on_dialog(dlg):
                try:
                    dialog_buffer.append(f"[{dlg.type}] {dlg.message}")
                    # accept 가 아닌 dismiss — confirm/beforeunload 의 OK 폭주 방지.
                    dlg.dismiss()
                except Exception:  # noqa: BLE001
                    pass
            def _hook_dialog(p):
                try:
                    p.on("dialog", _on_dialog)
                except Exception:  # noqa: BLE001
                    pass
            context.on("page", _hook_dialog)
            for _p in context.pages:
                _hook_dialog(_p)

            try:
                for idx, step in enumerate(scenario):
                    dialog_buffer.clear()
                    result = self._execute_step(
                        page, step, resolver, healer, artifacts
                    )
                    # 사이트가 인증 없는 접근에 native alert 대신 URL 쿼리 redirect
                    # (`?errorMsg=...`) 로 메시지를 전달하는 패턴 감지. dialog 와
                    # 같은 자리(노란 카드)에 합쳐 표시.
                    redirect_msg = _extract_redirect_msg(page.url or "")
                    if redirect_msg:
                        dialog_buffer.append(redirect_msg)
                    if dialog_buffer:
                        result.dialog_text = "\n".join(dialog_buffer)
                    results.append(result)
                    if headed and self.config.headed_step_pause_ms > 0:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                        time.sleep(self.config.headed_step_pause_ms / 1000.0)
                    if result.status == "FAIL":
                        # 최종 실패 스크린샷
                        fail_path = os.path.join(artifacts, "error_final.png")
                        self._safe_screenshot(page, fail_path)
                        break
                    # G-3: 스텝이 PASS/HEALED 로 판정됐어도 현재 page.url 이 봇 차단
                    # 페이지(/sorry/, captcha challenge 등) 면 마지막 레이어로 FAIL 처리.
                    # verify 가 없는 시나리오에서도 false positive 성공을 차단한다.
                    current_url = page.url or ""
                    if self._is_blocked_url(current_url):
                        log.error(
                            "[Step %s] 스텝은 %s 로 판정됐지만 현재 URL 이 봇 차단 페이지: %s",
                            step.get("step", "-"), result.status, current_url,
                        )
                        result.status = "FAIL"
                        fail_path = os.path.join(artifacts, "error_final.png")
                        self._safe_screenshot(page, fail_path)
                        break
                    # N. 새 탭 감지 — 검색 폼이 target=_blank 이거나 JS window.open
                    # 으로 새 탭/창에 결과를 열면 원래 page 는 변동 없음. 후속 스텝을
                    # 새 페이지에 적용하려면 여기서 전환해야 한다.
                    #
                    # O. chrome-error/about:blank 필터 — 네트워크 실패나 봇 차단으로
                    # 새 탭이 에러 페이지인 경우 전환하지 않고 무시 (유효 콘텐츠 없음).
                    # G-3 연장: 새 탭 URL 이 봇 차단 페이지여도 전환 안 함.
                    if len(context.pages) > 1 and context.pages[-1] is not page:
                        new_page = context.pages[-1]
                        try:
                            new_page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except Exception:
                            pass
                        new_url = new_page.url
                        if new_url.startswith(("chrome-error://", "about:blank", "data:text/html")):
                            log.warning(
                                "[Step %s] 새 탭이 에러/빈 페이지 (%s) — 전환 안 함. "
                                "사이트가 Playwright 봇 차단 또는 네트워크 문제.",
                                step.get("step", "-"), new_url,
                            )
                        elif self._is_blocked_url(new_url):
                            log.error(
                                "[Step %s] 새 탭이 봇 차단 페이지 (%s) — 전환 안 함 + 스텝 FAIL 처리.",
                                step.get("step", "-"), new_url,
                            )
                            result.status = "FAIL"
                            fail_path = os.path.join(artifacts, "error_final.png")
                            self._safe_screenshot(page, fail_path)
                            break
                        else:
                            log.info(
                                "[Step %s] 새 탭 감지 → 활성 페이지 전환 (%s → %s)",
                                step.get("step", "-"),
                                page.url, new_url,
                            )
                            page = new_page
                            try:
                                page.bring_to_front()
                            except Exception:
                                pass
                            # resolver/healer 의 내부 page 참조 rebind.
                            resolver.page = page
                            healer.page = page
                    # 스텝 간 random jitter — 봇 패턴(즉시 연속 액션) 회피.
                    # reCAPTCHA 등이 fill→press 100ms 이내 시퀀스를 트리거.
                    # 마지막 스텝 또는 max==0 이면 sleep 생략.
                    if (
                        idx < len(scenario) - 1
                        and self.config.step_interval_max_ms > 0
                    ):
                        jitter_s = random.uniform(
                            self.config.step_interval_min_ms,
                            self.config.step_interval_max_ms,
                        ) / 1000.0
                        time.sleep(jitter_s)

                # P-1. 모든 스텝 종료 후 final_state.png — 마지막 click 이 새 탭을
                # 열어 page 가 전환된 경우, 기존 step_N_*.png 는 전환 직전 화면만
                # 담는다. 여기서 최종 활성 페이지의 상태를 별도 캡처해 '실제로
                # 어디로 이동했는지' 시각 증거로 남긴다.
                try:
                    page.bring_to_front()
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                final_path = os.path.join(artifacts, "final_state.png")
                self._safe_screenshot(page, final_path)
                log.info("[Final] 최종 활성 페이지: %s → %s", page.url, final_path)

                # P-2. headed 모드에선 browser.close() 전에 짧게 대기 (사용자 시각 확인).
                if headed:
                    time.sleep(3)
            finally:
                # T-D / P0.1 — storage_state 덤프 (브라우저 종료 전, 인증 후 세션 보존)
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
        """단일 스텝을 실행하고 결과를 반환한다.

        흐름:
          1. 메타 액션 (navigate / wait / mock / auth_login / reset_state) — 즉시 처리
          2. 타겟 필요 액션 — 1차 시도 → 치유 1~4 → 휴리스틱 5~7 → FAIL
        """
        # ── 메타 액션 + LLM 보정 + 타겟-필요-액션 분기 ──
        meta = self._handle_meta_action(page, step, artifacts)
        if meta is not None:
            return meta

        # 타겟 필요 액션 — 1차 시도 + 다단계 자가 치유.
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        original_target = step.get("target")
        log.info("[Step %s] %s: %s", step_id, action, desc)

        # 1차 시도 (closed shadow / visibility heal 포함).
        result, verification_error = self._try_initial_target(
            page, step, resolver, artifacts,
        )
        if result is not None:
            return result

        # ── [치유 1단계] fallback_targets ──
        result, verr = self._try_fallback_targets(page, step, resolver, artifacts)
        if result is not None:
            return result
        if verr is not None:
            verification_error = verr

        # ── [치유 2단계] DSL action_alternatives (C) ──
        result, verr = self._try_action_alternatives(page, step, resolver, artifacts)
        if result is not None:
            return result
        if verr is not None:
            verification_error = verr

        if verification_error:
            ss = self._screenshot(page, artifacts, step_id, "fail")
            log.error("[Step %s] FAIL — verify 조건 불일치", step_id)
            return StepResult(
                step_id, action, str(original_target or ""),
                str(step.get("value", "")), desc,
                "FAIL", screenshot_path=ss,
            )

        # ── [치유 3단계] 로컬 DOM 유사도 매칭 ──
        result = self._try_local_healer(page, step, healer, resolver, artifacts)
        if result is not None:
            return result

        # ── [치유 4단계] Dify LLM 치유 ──
        result = self._try_dify_healer(page, step, resolver, artifacts)
        if result is not None:
            return result

        # ── [치유 5~7] 의미적 휴리스틱 체인 ──
        # 각 휴리스틱은 자기 의도(action+desc)에 맞을 때만 발동, 아니면 None 반환.
        for heuristic in (
            self._try_press_to_click_heuristic,
            self._try_first_result_heuristic,
            self._try_search_results_visible_heuristic,
            self._try_search_input_heuristic,
        ):
            r = heuristic(page, step, resolver, artifacts)
            if r is not None:
                return r

        # ── 모든 치유 실패 ──
        log.error("[Step %s] FAIL — 모든 치유 실패", step_id)
        return StepResult(
            step_id, action, str(original_target or ""),
            str(step.get("value", "")), desc,
            "FAIL",
        )

    # ─────────────────────────────────────────────────────────────────────
    # 메타 액션 + 1차 시도 helper — _execute_step 첫 부분 분리.
    # ─────────────────────────────────────────────────────────────────────

    def _handle_meta_action(
        self, page: Page, step: dict, artifacts: str,
    ) -> Optional[StepResult]:
        """타겟 불필요 / 별도 처리 액션을 즉시 실행해 StepResult 반환.

        해당 액션이 아니면 None 반환 (호출자가 일반 흐름 계속). 처리 대상:
            - navigate / maps          : page.goto + 다운로드 흡수
            - wait                     : page.wait_for_timeout
            - press (target 없음)      : page.keyboard.press
            - mock_status / mock_data  : _execute_mock_step
            - auth_login               : _execute_auth_login
            - reset_state              : _execute_reset_state
        """
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")

        if action in ("navigate", "maps"):
            return self._execute_navigate(page, step, artifacts)
        if action == "wait":
            ms = int(step.get("value", 1000))
            page.wait_for_timeout(ms)
            log.info("[Step %s] wait %dms -> PASS", step_id, ms)
            return StepResult(step_id, action, "", str(ms), desc, "PASS")

        # LLM 출력 보정 — normalize 후 다시 분기
        self._normalize_step(step)
        action = step["action"].lower()

        if action == "press" and not step.get("target"):
            key = step.get("value", "")
            page.keyboard.press(key)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] press '%s' (keyboard) -> PASS", step_id, key)
            return StepResult(step_id, action, "", key, desc, "PASS", screenshot_path=ss)

        if action in ("mock_status", "mock_data"):
            return self._execute_mock_step(page, step, artifacts)
        if action == "auth_login":
            return self._execute_auth_login(page, step, artifacts)
        if action == "reset_state":
            return self._execute_reset_state(page, step, artifacts)
        return None

    def _execute_navigate(
        self, page: Page, step: dict, artifacts: str,
    ) -> StepResult:
        """navigate / maps 액션 실행. download 응답은 PASS+heal=download_started."""
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        raw_url = step.get("value") or step.get("target", "")
        url = self._normalize_url(str(raw_url))
        if url != str(raw_url):
            log.info("[Step %s] URL 자동 normalize: %r → %r", step_id, raw_url, url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            if "Download is starting" in str(e):
                log.info("[Step %s] navigate -> PASS (다운로드 응답 — page 미로드)", step_id)
                try:
                    ss = self._screenshot(page, artifacts, step_id, "pass")
                except Exception:  # noqa: BLE001
                    ss = None
                return StepResult(
                    step_id, action, str(url), str(url), desc,
                    "PASS", heal_stage="download_started", screenshot_path=ss,
                )
            raise
        ss = self._screenshot(page, artifacts, step_id, "pass")
        log.info("[Step %s] navigate -> PASS", step_id)
        return StepResult(
            step_id, action, str(url), str(url), desc,
            "PASS", screenshot_path=ss,
        )

    def _try_initial_target(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> tuple[Optional[StepResult], Optional[VerificationAssertionError]]:
        """1차 시도 — original target 으로 locator 해석 + visibility heal + perform_action.

        ``ShadowAccessError`` 는 자동치유 의미 없으므로 즉시 FAIL StepResult 반환.
        verify 조건 실패는 caller 에 전달 (다음 단계가 끝까지 못 살리면 최종 FAIL 사유).
        """
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        original_target = step.get("target")
        try:
            locator = resolver.resolve(original_target)
        except ShadowAccessError as e:
            log.error("[Step %s] %s", step_id, e)
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return (
                StepResult(
                    step_id, action, str(original_target or ""),
                    str(step.get("value", "")), f"{desc} [closed shadow]",
                    "FAIL", screenshot_path=ss,
                ),
                None,
            )
        if not locator:
            return None, None
        # T-H — hidden element 면 ancestor hover / sibling swap 시도.
        swap = self._heal_visibility(page, locator, step_id)
        if swap is not None:
            locator = swap
        try:
            self._perform_action(page, locator, step, resolver)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            return (
                StepResult(
                    step_id, action, str(original_target or ""),
                    str(step.get("value", "")), desc,
                    "PASS", screenshot_path=ss,
                ),
                None,
            )
        except VerificationAssertionError as e:
            log.warning("[Step %s] verify 조건 실패: %s", step_id, e)
            return None, e
        except Exception as e:  # noqa: BLE001
            log.warning("[Step %s] 기본 타겟 실패: %s", step_id, e)
            return None, None

    # ─────────────────────────────────────────────────────────────────────
    # 치유 단계 helper — _execute_step 의 fallback / alternatives / local /
    # dify 단계를 단계별 메서드로 분리. 각 helper 는 성공 시 StepResult 반환,
    # 실패/미발동 시 None. fallback / alternatives 는 verify 실패가 누적될 수
    # 있어 (StepResult, VerificationAssertionError) 를 반환.
    # ─────────────────────────────────────────────────────────────────────

    def _try_fallback_targets(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> tuple[Optional[StepResult], Optional[VerificationAssertionError]]:
        """``step['fallback_targets']`` 의 selector 들을 순서대로 시도한다.

        성공 시 alias 등록 + step['target'] 갱신 (scenario.healed.json 기록 보존).
        verify 조건 실패는 누적해 caller 에 반환 (마지막 단계까지 다 실패하면 그
        verification_error 가 최종 FAIL 사유로 사용됨).
        """
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        original_target = step.get("target")
        verification_error: Optional[VerificationAssertionError] = None
        for fb_target in step.get("fallback_targets", []):
            fb_loc = resolver.resolve(fb_target)
            if not fb_loc:
                continue
            try:
                self._perform_action(page, fb_loc, step, resolver)
                resolver.record_alias(original_target, fb_target)
                step["target"] = fb_target
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] fallback 복구 성공: %s", step_id, fb_target)
                return (
                    StepResult(
                        step_id, action, str(fb_target),
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="fallback", screenshot_path=ss,
                    ),
                    None,
                )
            except VerificationAssertionError as e:
                verification_error = e
                log.warning("[Step %s] fallback verify 조건 실패: %s", step_id, e)
            except Exception:  # noqa: BLE001
                continue
        return None, verification_error

    def _try_action_alternatives(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> tuple[Optional[StepResult], Optional[VerificationAssertionError]]:
        """``step['action_alternatives']`` 의 등가 액션을 순서대로 시도한다 (C).

        Planner LLM 이 명시한 등가 액션 (예: press Enter → click 검색버튼).
        LocalHealer/Dify 보다 먼저 시도 — 명시 의도가 가장 신뢰도 높음.
        """
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        verification_error: Optional[VerificationAssertionError] = None
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
                    "[Step %s] action_alternatives 복구 성공: %s %s",
                    step_id, alt_step.get("action"), alt_step.get("target"),
                )
                return (
                    StepResult(
                        step_id, alt_step.get("action", action),
                        str(alt_step.get("target", "")),
                        str(alt_step.get("value", "")), desc,
                        "HEALED", heal_stage="alternative", screenshot_path=ss,
                    ),
                    None,
                )
            except VerificationAssertionError as e:
                verification_error = e
                log.warning(
                    "[Step %s] action_alternatives verify 조건 실패: %s", step_id, e,
                )
            except Exception:  # noqa: BLE001
                continue
        return None, verification_error

    def _try_local_healer(
        self, page: Page, step: dict, healer: LocalHealer,
        resolver: LocatorResolver, artifacts: str,
    ) -> Optional[StepResult]:
        """LocalHealer DOM 유사도 매칭으로 healed locator 시도."""
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        original_target = step.get("target")
        healed_loc = healer.try_heal(step)
        if not healed_loc:
            return None
        try:
            self._perform_action(page, healed_loc, step, resolver)
            ss = self._screenshot(page, artifacts, step_id, "healed")
            log.info("[Step %s] LocalHealer DOM 유사도 복구 성공", step_id)
            return StepResult(
                step_id, action, str(original_target or ""),
                str(step.get("value", "")), desc,
                "HEALED", heal_stage="local", screenshot_path=ss,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[Step %s] 로컬 치유 실행 실패: %s", step_id, e)
            return None

    def _try_dify_healer(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> Optional[StepResult]:
        """Dify LLM 치유 — 새 target/value/condition/action(whitelist) 받아 재시도."""
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        original_target = step.get("target")
        log.info(
            "[Step %s] Dify LLM 치유 요청 중 (timeout=%ds)...",
            step_id, self.config.heal_timeout_sec,
        )
        try:
            dom_snapshot = page.content()[: self.config.dom_snapshot_limit]
            new_target_info = self.dify.request_healing(
                error_msg=f"요소 탐색/실행 실패: {original_target}",
                dom_snapshot=dom_snapshot,
                failed_step=step,
                strategy_trace=[a.to_dict() for a in self._latest_strategy_trace],
            )
        except DifyConnectionError as e:
            log.error("[Step %s] Dify 치유 통신 실패: %s", step_id, e)
            return None
        if not new_target_info:
            return None
        # target / value / condition / fallback_targets 는 자유롭게 mutate 허용.
        # action 변경은 화이트리스트 전이만 허용 (false-PASS 위험 차단).
        allowed_keys = {"target", "value", "condition", "fallback_targets"}
        mutation = {k: v for k, v in new_target_info.items() if k in allowed_keys}
        proposed_action = new_target_info.get("action")
        if isinstance(proposed_action, str) and proposed_action.strip():
            proposed_action = proposed_action.strip().lower()
            old_action = str(step.get("action", "")).lower()
            if _is_allowed_action_transition(old_action, proposed_action):
                if proposed_action != old_action:
                    log.warning(
                        "[Step %s] Healer action 전이 허용: %s → %s (whitelist)",
                        step_id, old_action, proposed_action,
                    )
                mutation["action"] = proposed_action
            else:
                log.warning(
                    "[Step %s] Healer action 전이 거절: %s → %s (whitelist 외, false-PASS 위험)",
                    step_id, old_action, proposed_action,
                )
        step.update(mutation)
        healed_loc = resolver.resolve(step.get("target"))
        if not healed_loc:
            return None
        try:
            self._perform_action(page, healed_loc, step, resolver)
            resolver.record_alias(original_target, step.get("target"))
            ss = self._screenshot(page, artifacts, step_id, "healed")
            log.info(
                "[Step %s] LLM 치유 성공. 새 타겟: %s",
                step_id, step.get("target"),
            )
            return StepResult(
                step_id, str(step.get("action", action)),
                str(step.get("target", "")),
                str(step.get("value", "")), desc,
                "HEALED", heal_stage="dify", screenshot_path=ss,
            )
        except Exception as e:  # noqa: BLE001
            log.error("[Step %s] LLM 치유 후 실행 실패: %s", step_id, e)
            return None

    # ─────────────────────────────────────────────────────────────────────
    # 휴리스틱 helper — _execute_step 끝부분 단계별 분리.
    # 각 helper 는 발동 조건(action/desc 매칭) 만족 + 후보 selector 중 하나가
    # 성공하면 StepResult 를 반환하고, 그렇지 않으면 None.
    # ─────────────────────────────────────────────────────────────────────

    def _try_press_to_click_heuristic(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> Optional[StepResult]:
        """press(Enter/Return) 가 모두 실패했을 때 검색/제출 버튼 click 으로 시도 (B).

        '검색/search' 의도 맥락이면 click 후 navigation 효과 (URL 변경 or 유효
        새 탭) 까지 확인해서 chrome-error 새 탭 같은 봇 차단 산물을 false PASS 로
        흘려보내지 않는다 (E-1).
        """
        del resolver  # 본 휴리스틱은 selector 재매핑 안 함.
        action = step["action"].lower()
        if action != "press" or str(step.get("value", "")).lower() not in ("enter", "return"):
            return None
        step_id = step.get("step", "-")
        desc = step.get("description", "")
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
                    page, before_url, before_pages_count,
                ):
                    log.warning(
                        "[Step %s] press→click 후 유효한 navigation 없음 — 다음 후보 시도 (sel=%s)",
                        step_id, sel,
                    )
                    continue
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] press→click 휴리스틱 성공: %s", step_id, sel)
                return StepResult(
                    step_id, "click", sel, "", desc, "HEALED",
                    heal_stage="press_to_click", screenshot_path=ss,
                )
            except Exception:  # noqa: BLE001 — 후보 단순 skip
                continue
        return None

    def _try_first_result_heuristic(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> Optional[StepResult]:
        """click '첫 번째 결과/링크/항목' 의미적 휴리스틱 (E).

        description 에 '첫 결과' 의도가 있으면 main/article 영역의 첫 visible
        링크 시도. URL 변경 or 유효 새 탭을 반드시 확인해 false-PASS 차단 (E-4).
        """
        del resolver
        action = step["action"].lower()
        desc = step.get("description", "")
        if action != "click" or not self._matches_first_result_intent(desc):
            return None
        step_id = step.get("step", "-")
        for sel in self._FIRST_RESULT_CANDIDATES:
            try:
                loc = page.locator(sel)
                if loc.count() == 0:
                    continue
                before_url = page.url
                before_pages_count = len(page.context.pages)
                loc.first.click(timeout=3000)
                if not self._wait_for_navigation_effect(
                    page, before_url, before_pages_count,
                ):
                    log.warning(
                        "[Step %s] '첫 결과' 후보 click 후 navigation 없음 — 다음 후보 시도 (sel=%s)",
                        step_id, sel,
                    )
                    continue
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] '첫 결과' 휴리스틱 성공: %s", step_id, sel)
                return StepResult(
                    step_id, action, sel, "", desc, "HEALED",
                    heal_stage="first_result", screenshot_path=ss,
                )
            except Exception:  # noqa: BLE001
                continue
        return None

    def _try_search_results_visible_heuristic(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> Optional[StepResult]:
        """verify '검색결과 존재' 의미적 휴리스틱 (J).

        description 에 '검색 결과 (목록/존재/표시) 확인' + verify 일 때,
        main/article/검색결과 컨테이너 중 하나라도 visible 이면 PASS.
        """
        del resolver
        action = step["action"].lower()
        desc = step.get("description", "")
        if action != "verify" or not self._matches_search_results_intent(desc):
            return None
        step_id = step.get("step", "-")
        for sel in self._SEARCH_RESULTS_CANDIDATES:
            try:
                loc = page.locator(sel)
                if loc.count() == 0:
                    continue
                if loc.first.is_visible():
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] '검색결과 존재' 휴리스틱 성공: %s", step_id, sel)
                    return StepResult(
                        step_id, action, sel,
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="search_results_visible",
                        screenshot_path=ss,
                    )
            except Exception:  # noqa: BLE001
                continue
        return None

    def _try_search_input_heuristic(
        self, page: Page, step: dict, resolver: LocatorResolver, artifacts: str,
    ) -> Optional[StepResult]:
        """fill '검색창' 의미적 휴리스틱 (H).

        description 에 '검색' 키워드가 있으면 일반 search input selector 들로
        fallback — input[type=search] / [role=searchbox] / placeholder 매치 등.
        """
        action = step["action"].lower()
        desc = step.get("description", "")
        if action != "fill" or not self._matches_search_input_intent(desc):
            return None
        step_id = step.get("step", "-")
        original_target = step.get("target")
        for sel in self._SEARCH_INPUT_CANDIDATES:
            try:
                loc = page.locator(sel)
                if loc.count() == 0:
                    continue
                loc.first.fill(str(step.get("value", "")))
                resolver.record_alias(original_target, sel)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] '검색창' 휴리스틱 성공: %s", step_id, sel)
                return StepResult(
                    step_id, action, sel,
                    str(step.get("value", "")), desc,
                    "HEALED", heal_stage="search_input",
                    screenshot_path=ss,
                )
            except Exception:  # noqa: BLE001
                continue
        return None

    # B: press(Enter) 가 모든 치유 다 실패했을 때 click 으로 시도해볼 검색/제출 버튼 후보.
    # 가시성 필터와 한/영 라벨을 함께 고려. 우선순위는 좁은 것 → 넓은 것 순.
    _SEARCH_BUTTON_CANDIDATES = (
        "form[role=search] button:visible, [role=search] button:visible",
        "button[type=submit]:visible",
        "button[aria-label*='검색']:visible, button[aria-label*='Search' i]:visible",
        "button:has-text(/^(검색|Search|검색하기|Go|확인|Submit)$/i):visible",
        "[role=button]:has-text(/^(검색|Search|검색하기)$/i):visible",
    )

    # E: "첫 번째 검색결과/링크/항목" 의도 매칭 정규식 (한/영).
    # ordinal(첫/1번째/first/1st) ... result/link/item 패턴, 사이에 30자까지 허용.
    _FIRST_RESULT_RE = re.compile(
        r"(첫\s*번?째|\d+\s*번\s*째?|first|1st)"
        r".{0,30}?"
        r"(검색\s*결과|결과|링크|항목|아이템|result|link|item)",
        re.IGNORECASE | re.DOTALL,
    )

    @staticmethod
    def _matches_first_result_intent(desc: str) -> bool:
        """description 에서 '첫 N번째 결과/링크/항목' 의도를 감지한다."""
        return bool(QAExecutor._FIRST_RESULT_RE.search(desc or ""))

    # E: '첫 결과 클릭' 의도일 때 시도할 일반 셀렉터 후보.
    # 검색엔진별 정확한 검색결과 컨테이너 → 일반 시맨틱 → 광범위 fallback 순.
    # (검색엔진 컨테이너가 main 보다 정확 — 추천뉴스/광고 카드를 회피)
    _FIRST_RESULT_CANDIDATES = (
        "#main_pack a[href]:visible",       # Naver 통합검색 영역
        "#search a[href]:visible",           # Google 검색 영역
        "#web a[href]:visible",              # Yahoo 검색 영역
        "#results a[href]:visible",          # 일반
        "[id*='result' i] a[href]:visible",
        "[class*='result' i] a[href]:visible",
        "[id*='search' i] a[href]:visible",
        "[class*='search' i] a[href]:visible",
        "main a[href]:visible",              # 시맨틱 fallback
        "[role=main] a[href]:visible",
        "article a[href]:visible",
        "[role=article] a[href]:visible",
    )

    # H: "검색창에 입력" 의도 매칭 정규식 (한/영).
    # search 단어는 search bar / search box 등 명사구 우선, 'research' 오매칭 회피.
    _SEARCH_INPUT_RE = re.compile(
        r"검색\s*(창|박스|필드|입력|어\s*입력)|search\s*(box|bar|input|field)",
        re.IGNORECASE,
    )

    @staticmethod
    def _matches_search_input_intent(desc: str) -> bool:
        """description 에서 '검색창 입력' 의도를 감지한다."""
        return bool(QAExecutor._SEARCH_INPUT_RE.search(desc or ""))

    # J: "검색결과 (목록/존재/표시) 확인" 의도 매칭 정규식 (한/영).
    _SEARCH_RESULTS_RE = re.compile(
        r"검색\s*결과.*(목록|존재|표시|출력|확인|보이는)|"
        r"search\s*result.*(list|exist|visible|appear|show|display)",
        re.IGNORECASE | re.DOTALL,
    )

    @staticmethod
    def _matches_search_results_intent(desc: str) -> bool:
        """description 에서 '검색결과 존재 확인' 의도를 감지한다."""
        return bool(QAExecutor._SEARCH_RESULTS_RE.search(desc or ""))

    # G-1: 봇 차단 / captcha challenge / ratelimit 페이지 URL 패턴.
    # URL 변경 자체는 일어났어도 "의도된 목적지" 가 아니므로 성공으로 인정하면 안 된다.
    # 대표 사례:
    #   - Google 봇 차단     : google.com/sorry/index?continue=... ("unusual traffic")
    #   - Google reCAPTCHA   : /recaptcha/
    #   - Cloudflare 챌린지  : /cdn-cgi/challenge-platform/
    #   - Amazon 봇 체크     : /errors/validateCaptcha, /robot-check
    #   - 일반 rate limit    : /blocked, /ratelimit, /too-many-requests, /429
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
        """URL 이 봇 차단/captcha/ratelimit 페이지인지 판정."""
        return bool(QAExecutor._BLOCKED_URL_RE.search(url or ""))

    # E-2: 검색결과 페이지 URL 패턴. 쿼리스트링에 검색어 key, 또는 /search/ /results/ /find/ path.
    # 이 패턴에 매치 안 되면 "검색 결과 페이지에 있다" 고 간주할 수 없다.
    _SEARCH_RESULT_URL_RE = re.compile(
        r"[?&](q|p|query|search|keyword|wd|k|term|s|searchterm)=|"
        r"/search[/?]|/results?[/?]|/find[/?]|/web[/?]|/results?$|/search$",
        re.IGNORECASE,
    )

    @staticmethod
    def _had_navigation_effect(
        page: Page, before_url: str, before_pages_count: int
    ) -> bool:
        """click/press 후 실제로 navigation 효과가 있었는지 판정.

        효과 = (a) 현재 페이지 URL 이 변경되었거나, (b) 유효한 새 탭이 열렸다.
        chrome-error:// / about:blank / data: 새 탭은 봇 차단 산물이므로 효과 없음.
        G-2: 봇 차단(/sorry/ 등) 이나 captcha challenge URL 도 효과 없음으로 간주.
        URL 은 변경됐지만 "의도된 목적지" 가 아니므로 false positive 차단.

        Args:
            page: 현재 활성 Playwright Page.
            before_url: 액션 직전의 page.url.
            before_pages_count: 액션 직전의 context.pages 길이.

        Returns:
            유효한 네비게이션 효과가 있었으면 True.
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
        """polling 으로 최대 ``deadline_sec`` 초까지 navigation 효과를 대기한다."""
        deadline = time.time() + deadline_sec
        while time.time() < deadline:
            if QAExecutor._had_navigation_effect(page, before_url, before_pages_count):
                return True
            page.wait_for_timeout(100)
        return QAExecutor._had_navigation_effect(page, before_url, before_pages_count)

    # J: '검색결과 존재 확인' 의도일 때 visible 인지 체크할 후보 컨테이너.
    # 하나라도 visible 이면 검색결과가 있다고 간주.
    #
    # 주의: main / [role=main] / article 은 검색 전 홈페이지에서도 항상 visible
    # 이라 false positive PASS 를 만든다 (Yahoo 검색 실패 시 홈의 main 이 잡힘).
    # 반드시 "검색결과" 의도를 가진 검색엔진 전용 컨테이너 또는 id/class 에
    # 'result' 가 포함된 것만 유효로 취급한다.
    _SEARCH_RESULTS_CANDIDATES = (
        "#main_pack",                     # Naver 통합검색
        "#search",                          # Google 검색
        "#results",                         # 일반
        "#web",                             # Yahoo 검색결과
        "[id*='result' i]",
        "[class*='search-result' i]",
        "[class*='results' i]",
        "[data-testid*='result' i]",
    )

    # H: '검색창 fill' 의도일 때 시도할 일반 search input 후보.
    # 시맨틱(type=search/role=searchbox) 우선, placeholder/aria-label 매치 다음,
    # 마지막으로 검색엔진별 흔한 name 속성(q, p, query, search, wd 등).
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

    # ── LLM 출력 보정 ──
    KNOWN_KEYS = {
        "enter", "tab", "escape", "backspace", "delete", "arrowup",
        "arrowdown", "arrowleft", "arrowright", "space", "home", "end",
        "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f6",
        "f7", "f8", "f9", "f10", "f11", "f12",
    }

    # 사설망/로컬 IP 패턴 — 자동 normalize 시 https 가 아닌 http 적용
    _LOCAL_HOST_PREFIXES = ("localhost", "127.", "0.0.0.0", "10.", "192.168.", "172.16.",
                            "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
                            "172.22.", "172.23.", "172.24.", "172.25.", "172.26.",
                            "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")
    _IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/.*)?$")

    @staticmethod
    def _normalize_url(raw: str) -> str:
        """스킴 없는 URL 에 자동으로 https:// (또는 로컬은 http://) 를 붙인다.

        사용자가 Jenkins 파라미터에 ``www.naver.com`` 만 넣거나 LLM 이 스킴 없이
        반환해도 ``page.goto()`` 의 'invalid URL' 에러를 막는다.

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
        LLM이 생성한 DSL 스텝의 흔한 오류를 자동 보정한다.
        - press: target에 키 이름이 들어가고 value가 비어 있는 경우 swap
        - navigate: value가 비고 target에 URL이 있는 경우 swap
        """
        action = step.get("action", "").lower()
        target = str(step.get("target", "")).strip()
        value = str(step.get("value", "")).strip()

        if action == "press" and not value and target.lower() in QAExecutor.KNOWN_KEYS:
            step["value"] = target
            step["target"] = ""
            log.debug("[보정] press: target '%s' → value로 이동", target)

        # navigate 의 흔한 LLM 실수: URL 을 target 에 넣음.
        # 스킴 없어도 'foo.com', 'localhost:3000' 등 URL 같으면 swap.
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
                log.debug("[보정] navigate: target → value로 이동")

    def _resolve_upload_path(self, raw_path) -> str:
        """upload.value 를 artifacts 루트 아래의 실제 파일 경로로 해석한다."""
        value = str(raw_path or "").strip()
        if not value:
            raise ValueError("upload.value 가 비어 있음")

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
            f"업로드 파일을 찾을 수 없거나 허용 루트 밖 경로임: {value!r} "
            f"(허용 루트: {allowed_root})"
        )

    @staticmethod
    def _normalize_mock_body(value) -> str:
        """mock_data.value 를 application/json body 문자열로 정규화한다."""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)

        raw = str(value or "").strip()
        if not raw:
            raise ValueError("mock_data.value 가 비어 있음")
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
        """API 모킹 라우트를 설치한다.

        Args:
            page: Playwright Page.
            url_pattern: glob 또는 정규식 URL 패턴.
            status: 응답 status code (mock_status 용).
            body: 응답 JSON body 문자열 (mock_data 용).
            times: 라우트가 몇 번 매칭될 때까지 가로챌지. **기본값 1** —
                후속 스텝 전역 오염을 막기 위함. step.value 와 별도로 step
                dict 에 ``"times"`` 키가 있으면 호출자가 이를 전달해 폴링/
                재시도 시나리오를 모킹할 수 있다.
        """
        pattern = str(url_pattern or "").strip()
        if not pattern:
            raise ValueError("mock_* action 에 target(URL 패턴)이 필요함")
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
            log.warning("[MockGuard] MOCK_OVERRIDE=1 — mock scope guard 우회: %s", pattern)
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
                "mock_* target 이 너무 넓어 false positive 위험이 큼: "
                f"{pattern!r}. MOCK_OVERRIDE=1 로만 명시 우회 가능"
            )

        for host in blocked_hosts:
            if host and host in normalized:
                raise ValueError(
                    "mock_* target 이 차단된 host 와 매칭됨: "
                    f"host={host!r}, pattern={pattern!r}. "
                    "MOCK_OVERRIDE=1 로만 명시 우회 가능"
                )

    def _execute_mock_step(
        self, page: Page, step: dict, artifacts: str
    ) -> StepResult:
        """mock_status / mock_data 스텝을 실행한다.

        DOM 이 아니라 URL 패턴이 입력이므로 LocalHealer 와 fallback_targets
        DOM 매칭은 적용되지 않는다. 대신 다음 2단계 치유를 지원한다:

        1. ``fallback_targets`` 가 대체 URL 패턴 문자열을 담고 있으면 순서대로 시도.
        2. 위가 모두 실패하면 Dify LLM 치유 (yaml 의 healer 프롬프트가 mock_* 전용
           가이드를 가진다 — 해당 분기를 활성화).

        ``step["times"]`` 가 정수면 mock 라우트의 매칭 횟수를 제어한다 (기본 1).
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
            log.warning("[Step %s] mock 설치 실패: %s — fallback 시도", step_id, e)

        # 1단계: fallback_targets (대체 URL 패턴)
        for fb_target in step.get("fallback_targets", []) or []:
            try:
                fb_step = {**step, "target": str(fb_target)}
                self._apply_mock_route(page, fb_step)
                step["target"] = str(fb_target)  # healed.json 반영
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] mock fallback 패턴 복구: %s", step_id, fb_target)
                return StepResult(
                    step_id, action, str(fb_target), str(step.get("value", "")), desc,
                    "HEALED", heal_stage="fallback", screenshot_path=ss,
                )
            except ValueError:
                continue

        # 2단계: Dify LLM 치유 (URL 패턴/value 교정)
        try:
            new_target_info = self.dify.request_healing(
                error_msg=f"mock 설치 실패: {original_target}",
                dom_snapshot="",  # mock_* 는 DOM 무관 — 빈 컨텍스트로 호출
                failed_step=step,
                strategy_trace=[a.to_dict() for a in self._latest_strategy_trace],
            )
        except DifyConnectionError as e:
            log.error("[Step %s] Dify 치유 통신 실패: %s", step_id, e)
            new_target_info = None

        if new_target_info:
            step.update(new_target_info)
            try:
                self._apply_mock_route(page, step)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info("[Step %s] mock LLM 치유 성공: %s", step_id, step.get("target"))
                return StepResult(
                    step_id, action, str(step.get("target", "")),
                    str(step.get("value", "")), desc,
                    "HEALED", heal_stage="dify", screenshot_path=ss,
                )
            except ValueError as e:
                log.error("[Step %s] LLM 치유 후에도 mock 실패: %s", step_id, e)

        ss = self._screenshot(page, artifacts, step_id, "fail")
        return StepResult(
            step_id, action, original_target, str(step.get("value", "")), desc,
            "FAIL", screenshot_path=ss,
        )

    def _apply_mock_route(self, page: Page, step: dict) -> None:
        """step dict 의 action/target/value/times 를 _install_mock_route 로 변환."""
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
        """reset_state 액션 — 시나리오 도중 client-side 상태를 비운다.

        DSL 형태:
          {"action": "reset_state", "target": "", "value": "cookie"}     # 쿠키만
          {"action": "reset_state", "target": "", "value": "storage"}    # local + session
          {"action": "reset_state", "target": "", "value": "indexeddb"}  # IDB
          {"action": "reset_state", "target": "", "value": "all"}        # 위 셋 모두

        value 화이트리스트는 `__main__._RESET_STATE_VALID_VALUES` 와 동기.
        BrowserContext / Page level API 만 사용 — 백엔드 hook 없이 자체 완결.
        """
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        scope = str(step.get("value", "")).strip().lower()

        try:
            if scope in ("cookie", "all"):
                page.context.clear_cookies()
                log.info("[Step %s] reset_state cookie -> cleared", step_id)

            if scope == "all":
                # docs/PLAN_PRODUCTION_READINESS.md §"T-B Day 2" — all 은
                # cookie + storage + indexeddb + permissions reset 까지 포함.
                # geolocation/notifications/clipboard 등 grant 된 권한 초기화.
                try:
                    page.context.clear_permissions()
                    log.info("[Step %s] reset_state permissions -> cleared", step_id)
                except Exception as e:  # noqa: BLE001
                    # 일부 Playwright 버전 / 컨텍스트는 미지원 — soft fail.
                    log.warning(
                        "[Step %s] reset_state permissions 미지원 (skip): %s",
                        step_id, e,
                    )

            if scope in ("storage", "all"):
                # localStorage / sessionStorage 는 SecurityError 가 about:blank
                # 같은 origin 없는 페이지에서 발생할 수 있어 try 안에서 처리.
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
                        } catch (e) { /* no-op — Safari 등 미지원 시 */ }
                    }"""
                )
                log.info("[Step %s] reset_state indexeddb -> cleared", step_id)

        except Exception as e:  # noqa: BLE001
            log.error("[Step %s] reset_state %s 실패: %s", step_id, scope, e)
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
        """auth_login 액션 — form / totp / oauth 모드 분기.

        DSL 형태:
          {"action": "auth_login", "target": "form", "value": "<credential_alias>"}
          {"action": "auth_login", "target": "totp", "value": "<credential_alias>"}
          {"action": "auth_login", "target": "form, email_field=#email, password_field=#pw, submit=#login",
           "value": "<credential_alias>"}

        credential 은 환경변수 `AUTH_CRED_<ALIAS>_USER` / `_PASS` / `_TOTP_SECRET`
        에서 lookup. 자세한 spec 은 zero_touch_qa.auth 모듈 docstring 참조.
        """
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        target_str = str(step.get("target", ""))
        alias = str(step.get("value", ""))

        opts = parse_auth_target(target_str)
        try:
            cred = resolve_credential(alias)
        except CredentialError as e:
            log.error("[Step %s] auth_login credential 실패: %s", step_id, e)
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
            # T-D Phase 5 — OAuth mock server 통합 후 활성화
            log.error(
                "[Step %s] auth_login oauth 모드는 T-D Phase 5 (mock OAuth) 미완료",
                step_id,
            )
            ss = self._screenshot(page, artifacts, step_id, "fail")
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        log.error("[Step %s] auth_login 알 수 없는 mode=%r", step_id, opts.mode)
        ss = self._screenshot(page, artifacts, step_id, "fail")
        return StepResult(
            step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
            "FAIL", screenshot_path=ss,
        )

    def _auth_login_form(
        self, page: Page, step: dict, opts: AuthOptions, cred: Credential,
        artifacts: str,
    ) -> StepResult:
        """form 로그인 — email + password 필드 채우고 submit 클릭."""
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        target_str = str(step.get("target", ""))
        alias = str(step.get("value", ""))

        # 민감 input locator 를 미리 잡아 mask 리스트로 사용. fill 전이라도
        # _find_auth_field 가 RuntimeError 일 수 있어 None 으로 초기화하고 try 안에서 갱신.
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
            log.error("[Step %s] auth_login form 실패: %s", step_id, e)
            ss = self._screenshot_masked(
                page, artifacts, step_id, "fail",
                mask=[loc for loc in (email_loc, pwd_loc) if loc is not None],
            )
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        # P0.1 #3 — credential 평문이 PASS 스크린샷에 남지 않도록 입력 필드를 mask.
        # submit 후 navigation 으로 detached 된 locator 는 Playwright 내부에서 no-op.
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
        """TOTP 로그인 — pyotp 로 6자리 코드 생성 후 입력."""
        step_id = step.get("step", "-")
        desc = step.get("description", "")
        target_str = str(step.get("target", ""))
        alias = str(step.get("value", ""))

        if not cred.has_totp():
            log.error(
                "[Step %s] auth_login totp 실패 — alias '%s' 에 TOTP 시크릿 없음",
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
            # submit — 별도 버튼 있으면 클릭, 없으면 그대로 (auto-submit form 가정)
            submit_loc = self._try_find_auth_field(
                page, opts.submit, SUBMIT_BUTTON_CANDIDATES,
            )
            if submit_loc is not None:
                submit_loc.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception as e:
            log.error("[Step %s] auth_login totp 실패: %s", step_id, e)
            ss = self._screenshot_masked(
                page, artifacts, step_id, "fail",
                mask=[loc for loc in (otp_loc,) if loc is not None],
            )
            return StepResult(
                step_id, "auth_login", target_str, mask_secret(alias, keep=0), desc,
                "FAIL", screenshot_path=ss,
            )

        # P0.1 #3 — TOTP 코드가 PASS 스크린샷에 남지 않도록 마스킹.
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
        """explicit selector 가 있으면 그것, 없으면 후보 selector 순서대로 시도.

        매치 0건이면 RuntimeError. 첫 일치하는 element 의 ``.first`` 반환.
        """
        if explicit:
            loc = page.locator(explicit)
            try:
                if loc.count() > 0:
                    return loc.first
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"auth_login {field_name} field 매치 0 (explicit={explicit!r})")
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    return loc.first
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError(
            f"auth_login {field_name} field 자동 탐지 실패 — 후보: {list(candidates)}"
        )

    @staticmethod
    def _try_find_auth_field(
        page: Page, explicit: Optional[str], candidates: tuple,
    ) -> Optional[Locator]:
        """``_find_auth_field`` 의 optional 버전 — 미발견 시 RuntimeError 대신 None."""
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
        """기존 verify 호환을 위해 text_content 와 input_value 를 모두 고려한다."""
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
                f"텍스트/값 불일치: 기대='{expected}', 실제='{actual}'"
            )

    # ── A: action 별 strategy chain (multi-strategy + post-condition) ──
    #
    # 동기: 단일 매핑 강제 (예: select_option(label=...)) 가 LLM healer 로도 못 고치는
    # 클래스의 실패를 만든다. 각 액션이 자체적으로 여러 매핑/형태를 시도하고 직접
    # 결과를 검증하면, healer 호출 전에 결정적으로 회복 가능한 케이스를 모두 흡수한다.
    #
    # 시도 결과는 ``self._latest_strategy_trace`` 에 누적되어 Dify healer 호출 시
    # 컨텍스트로 주입된다 ("selector 만 바꿔도 같은 timeout 이었다" 정보 보존).

    @staticmethod
    def _normalize_check_state(value) -> bool:
        s = str(value or "").strip().lower()
        if s in ("false", "off", "no", "0", "uncheck", "unchecked"):
            return False
        # 빈 값은 default = check
        return True

    def _do_select(self, locator: Locator, value: str) -> None:
        """select 다중 전략. positional → value= → label= 순. post-check: 실 selected."""
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
                    f"select post-check 실패: 기대={value!r}, "
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
        raise last_err if last_err else RuntimeError("select 모든 전략 실패")

    def _do_check(self, locator: Locator, value: str) -> None:
        """check 다중 전략. native → click 토글 → JS force-set. post-check: is_checked()."""
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
        raise last_err if last_err else RuntimeError("check 모든 전략 실패")

    def _upload_path_candidates(self, value: str) -> list[tuple[str, str]]:
        """upload.value 를 여러 경로 변형으로 확장. (전략명, 절대경로) 리스트.

        마지막 후보는 항상 ``upload_sample.txt`` default 더미 — LLM 이 ``test.txt``
        같은 placeholder 를 emit 했을 때도 결정적으로 PASS 시키기 위함.
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

        # default 더미 fallback — Pipeline 이 artifacts 안에 ``upload_sample.txt`` 를
        # 미리 생성하므로 항상 존재한다. LLM 의 placeholder value 도 결정적으로 흡수.
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
        """upload 다중 전략. 후보 경로 순회 + post-check (input.value endswith basename)."""
        if not value:
            raise ValueError("upload.value 가 비어 있음")

        trace: list[_StrategyAttempt] = []
        last_err: Exception | None = None

        for name, path in self._upload_path_candidates(value):
            if not os.path.exists(path):
                trace.append(_StrategyAttempt(name, f"not found: {path}"))
                continue
            # 보안 가드: artifacts root 또는 SCRIPTS_HOME 하위만 허용
            allowed_roots = [os.path.abspath(self.config.artifacts_dir)]
            sh = os.environ.get("SCRIPTS_HOME") or ""
            if sh:
                allowed_roots.append(os.path.abspath(sh))
            if not any(path.startswith(root + os.sep) or path == root for root in allowed_roots):
                trace.append(_StrategyAttempt(
                    name, f"보안 가드: 허용 루트 밖 — {path}"
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
            f"업로드 후보 경로 모두 사용 불가: {value!r}"
        )

    def _do_fill(self, locator: Locator, value: str) -> None:
        """fill 다중 전략. clear+fill → type → JS evaluate. post-check: input_value()."""
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
        raise last_err if last_err else RuntimeError("fill 모든 전략 실패")

    # ── 14대 DSL 액션 수행 ──
    def _perform_action(
        self, page: Page, locator: Locator, step: dict, resolver: LocatorResolver
    ):
        """14대 DSL 액션을 실제 Playwright 동작으로 수행한다.

        Args:
            page: Playwright Page (verify 에서 사용).
            locator: 대상 요소의 Playwright Locator.
            step: DSL 스텝 dict. ``action`` 과 ``value`` 키를 참조한다.
            resolver: drag 목적지 같은 추가 target 을 해석할 LocatorResolver.

        Raises:
            ValueError: 미지원 액션일 때.
            VerificationAssertionError: verify 액션에서 조건 불일치 시.
        """
        action = step["action"].lower()
        value = step.get("value", "")
        # A: 매 step 마다 strategy trace reset. healer 가 마지막 step 의 trace 만 본다.
        self._latest_strategy_trace = []

        if action == "click":
            # 1) viewport 안으로 명시적 스크롤 (best-effort, 실패 무시).
            #    Playwright 가 click 시 자동 스크롤하긴 하지만 동적 재배치가 잦은
            #    페이지(Yahoo 광고 등)에서 stability 못 잡고 timeout 나는 케이스 회피.
            # 2) timeout 5s → 10s — 광고/이미지 lazy load 로 stability 늦은 페이지 대응.
            try:
                locator.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            # P-3. 클릭 대상의 href 미리 로깅 — stretched-box 같은 invisible overlay
            # 를 클릭해 navigation 이 일어날 경우 어느 링크였는지 사후 추적 가능.
            try:
                target_href = locator.get_attribute("href", timeout=1000)
                target_text = (locator.text_content(timeout=1000) or "").strip()[:60]
                if target_href or target_text:
                    log.info("[Click] href=%r text=%r", target_href, target_text)
            except Exception:
                pass
            # E-3: "첫 결과/링크/항목" click 은 **반드시** navigation 효과가 있어야 한다.
            # Yahoo 홈의 stretched-box overlay 처럼 click 자체는 성공해도 이동이 안 되는
            # false positive 를 막는다. 효과 없으면 RuntimeError → fallback chain 진행.
            desc = str(step.get("description", ""))
            need_nav = QAExecutor._matches_first_result_intent(desc)
            before_url = page.url if need_nav else ""
            before_pages_count = len(page.context.pages) if need_nav else 0
            try:
                locator.click(timeout=10000)
            except Exception as click_err:
                # T-H (G) — Playwright click actionability 거부 (height:0 / outside
                # viewport / hidden) 케이스 마지막 수단. element 가 anchor/button
                # 류일 때만 JS dispatchEvent('click') 시도. ktds.com 처럼 GNB link
                # 의 computed style 이 height:0 / line-height:0 라 normal click 이
                # 영원히 actionability 거부하는 사이트 대응.
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
                    "[Click] Playwright click 거부 (%s) → JS dispatch click 폴백 시도",
                    msg.split("\n", 1)[0][:120],
                )
                locator.evaluate("el => el.click()")
            if need_nav and not QAExecutor._wait_for_navigation_effect(
                page, before_url, before_pages_count
            ):
                raise RuntimeError(
                    f"'첫 결과' click 후 navigation 없음 — "
                    f"URL 유지({before_url}), 유효한 새 탭 없음. "
                    f"링크가 overlay 에 가려졌거나 봇 차단 가능성."
                )
        elif action == "fill":
            # A: multi-strategy + post-condition (clear+fill / type / js-set).
            self._do_fill(locator, str(value))
        elif action == "press":
            # M+N. post-press 검증 — press Enter + '검색' 의도 맥락이면 둘 중
            # 하나여야 진짜 submit 된 것: (a) URL 변경, (b) 새 탭/창이 열리고
            # 그 URL 이 유효한 콘텐츠 페이지 (chrome-error/about:blank 아님).
            # 둘 다 없으면 예외 던져 fallback/alternatives/B 휴리스틱 진행.
            #
            # chrome-error 필터를 추가한 이유: Yahoo 등이 봇으로 판정해 폼 submit
            # 을 차단하면 새 탭이 chrome-error 로 뜨는데, 그걸 "submit 성공"으로
            # 오판하면 후속 verify/click 이 원래 홈페이지 기준으로 false positive PASS.
            before_url = page.url
            context = page.context
            before_pages = len(context.pages)
            locator.press(str(value))
            if str(value).lower() in ("enter", "return"):
                desc = str(step.get("description", ""))
                # 검색 폼에 대한 anti-flake 휴리스틱 — 외부 검색 사이트가 봇 차단으로
                # chrome-error 새 탭을 띄우면 후속 verify 가 false PASS 되는 것을 방지.
                # 단, localhost/file:// 같은 fixture 환경은 단순 DOM 업데이트(예: #echo
                # 텍스트 변경)만 하는 것이 정상이므로 strict 검사 대상에서 제외한다.
                # 후속 verify step 이 실제 동작을 검증하므로 여기서 막을 필요 없음.
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
                        new_tab_urls = [pg.url for pg in new_pages] or ["(없음)"]
                        raise RuntimeError(
                            f"press Enter 후 검색 제출 실패 — "
                            f"URL 유지({before_url}) + 유효한 새 탭 없음. "
                            f"새 탭 URL: {new_tab_urls} "
                            f"(chrome-error/about:blank 은 봇 차단으로 간주)"
                        )
        elif action == "upload":
            # A: 후보 경로 다중 전략 + post-condition (input.value endswith basename).
            self._do_upload(locator, str(value))
        elif action == "drag":
            target_locator = resolver.resolve(value)
            if not target_locator:
                raise RuntimeError(f"drag 목적지 탐색 실패: {value!r}")
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
            # A: multi-strategy + post-condition. positional → value= → label= 순.
            self._do_select(locator, str(value))
        elif action == "check":
            # A: multi-strategy + post-condition. native → click → JS force-set.
            self._do_check(locator, str(value))
        elif action == "hover":
            locator.hover()
        elif action == "verify":
            # E-2: "검색결과 (목록/존재/표시) 확인" 의도 verify 는 **URL 자체가
            # 검색결과 페이지** 여야 한다. 봇 차단으로 홈에 머문 상태에서
            # 홈의 임의 요소(main/article)가 visible 이라고 false PASS 내는 것 차단.
            desc = str(step.get("description", ""))
            if QAExecutor._matches_search_results_intent(desc):
                current_url = page.url or ""
                if not QAExecutor._SEARCH_RESULT_URL_RE.search(current_url):
                    raise VerificationAssertionError(
                        f"검색결과 verify 실패 — 현재 URL 이 검색결과 페이지가 아님: "
                        f"{current_url} "
                        f"(검색 제출이 실제로 이뤄졌는지 이전 스텝 확인 필요)"
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
                elif condition in ("url_contains", "url_not_contains"):
                    # target 은 무시 — page.url 자체를 검사. 합성된 tour
                    # 시나리오의 "로그인 안내 페이지로 바운스 안 됐는지" 확인 등.
                    current_url = page.url or ""
                    needle = str(value)
                    if condition == "url_contains" and needle not in current_url:
                        raise VerificationAssertionError(
                            f"URL 에 '{needle}' 포함되지 않음: {current_url}"
                        )
                    if condition == "url_not_contains" and needle in current_url:
                        raise VerificationAssertionError(
                            f"URL 에 '{needle}' 포함됨 (포함되면 안 됨): {current_url}"
                        )
                elif condition == "min_text_length":
                    # 본문 텍스트 최소 길이 — '비어있는 화면 / 로그인 안내 페이지'
                    # 같은 빈 응답을 잡아내는 용도.
                    try:
                        body_text = locator.inner_text(timeout=5000)
                    except Exception:
                        body_text = ""
                    body_text = body_text.strip()
                    try:
                        threshold = int(value)
                    except (TypeError, ValueError):
                        threshold = 0
                    if len(body_text) < threshold:
                        raise VerificationAssertionError(
                            f"본문 텍스트 길이 {len(body_text)} < {threshold} (비어있는 화면 의심)"
                        )
                else:
                    raise ValueError(
                        f"미지원 verify.condition: {condition!r} "
                        f"(허용: visible, hidden, disabled, enabled, checked, value, "
                        f"text, url_contains, url_not_contains, min_text_length)"
                    )
            except AssertionError as e:
                raise VerificationAssertionError(str(e)) from e
        else:
            raise ValueError(
                f"미지원 DSL 액션: '{action}'. "
                "허용: navigate, click, fill, press, select, check, hover, wait, "
                "verify, upload, drag, scroll, mock_status, mock_data"
            )

    # ── Visibility Healer (T-H) ──
    # codegen 이 hover-then-click sequence 의 hover 를 빠뜨려 element 가 hidden
    # 인 상태로 click 시도되는 케이스. ancestor 중 hoverable 후보 (aria-haspopup
    # / role=menu / nav / dropdown class / :hover CSS rule) 를 찾아 hover 후
    # 재검사한다. 1차 시도 직전에만 호출 — 정상 케이스(이미 visible)엔 영향 0.

    def _heal_visibility(
        self, page: Page, locator: Locator, step_id,
    ) -> Optional[Locator]:
        """element 가 hidden 이면 5단계로 visible 화 시도.

        순서 (각 단계는 visible 되면 즉시 단축):
          (1) `scroll_into_view_if_needed` — Intersection Observer 트리거 사이트.
          (2) cascade ancestor hover — `_VISIBILITY_HEALER_JS` 가 추출한
              hoverable 후보를 outermost → innermost 순서로 누적 hover. 다단
              메뉴 (회사소개 > 회사연혁 > ~2013) 는 outer 부터 hover 해야 다음
              level 이 visible 됨.
          (3) page-level activator probe — `<header>`/`<nav>`/`<main>`/`<body>` hover.
              사이트 전역 hover 이벤트로 menu 활성화하는 케이스.
          (4) size-aware poll — bounding_box.height/width > 0 가 될 때까지
              최대 2s 대기. 폰트/CSS 비동기 로딩으로 늦게 expand 되는 사이트.
          (5) sibling swap — `filter(visible=True).first` 로 visible 매치 교체.

        모든 단계 합산 한도 ~6s. 정상 visible element 에는 (0) 검사만 발생.

        Returns:
            visible 한 다른 형제 매치를 찾았으면 그 Locator. 그 외 None
            (locator 자체를 그대로 사용해도 OK 임을 의미).
        """
        try:
            if locator.is_visible():
                return None
        except Exception:
            return None  # locator 가 invalid 한 케이스는 후속 healer 가 처리

        # ── (1) D — scroll_into_view ─────────────────────────────────────
        # Playwright 가 viewport 에 element 를 가져옴. Intersection Observer
        # 기반 lazy menu 가 펼쳐지는 케이스에 효과적. 0-size 도 위치만 있으면
        # 동작. 실패는 silent (다음 단계로).
        try:
            locator.scroll_into_view_if_needed(timeout=1500)
            page.wait_for_timeout(150)
            if locator.is_visible():
                log.info("[Step %s] visibility-healer 복구 — scroll_into_view", step_id)
                return None
        except Exception:
            pass

        # ── (2) cascade ancestor hover (outermost → innermost) ──────────
        # `_VISIBILITY_HEALER_JS` 는 leaf 에서 위로 walk → candidates[0] 가
        # leaf 에 가장 가깝고 [-1] 이 outermost. 다단 hover 메뉴 (예: ktds.com
        # 의 회사소개 > 회사연혁 > ~2013) 는 outermost 부터 차례로 hover 해야
        # 각 단계의 :hover 가 cascade 되어 다음 trigger 가 visible 해진다.
        # 단일 ancestor hover 는 1-level 만 풀고 2-level+ 에서 실패.
        # Playwright hover() 는 mouse 를 element 중심으로 이동 — 다음 hover 가
        # descendant 라면 ancestor 의 :hover 는 자동 유지 (browser 동작).
        try:
            candidates = locator.evaluate(_VISIBILITY_HEALER_JS)
        except Exception as e:  # noqa: BLE001
            log.debug("[Step %s] visibility-healer evaluate 실패: %s", step_id, e)
            candidates = []

        chain = list(reversed(candidates))[:5]  # 최대 5단계 cascade
        hovered_path: list[str] = []
        for cand in chain:
            sel = cand.get("path") or ""
            reason = cand.get("reason") or "unknown"
            if not sel:
                continue
            try:
                ancestor = page.locator(sel).first
                ancestor.hover(timeout=1500)
                page.wait_for_timeout(150)  # 메뉴 transition
                hovered_path.append(f"{sel}({reason})")
                if locator.is_visible():
                    log.info(
                        "[Step %s] visibility-healer 복구 — cascade hover %s",
                        step_id, " > ".join(hovered_path),
                    )
                    return None
            except Exception:  # noqa: BLE001
                continue

        # ── (3) E — page-level activator probe ──────────────────────────
        # 사이트 전체에 mousemove/hover 이벤트를 주어 사용자 상호작용 시작을
        # 시뮬레이션. ktds.com 같이 GNB 가 lazy expand 되는 케이스에서 header
        # 영역 hover 만으로 menu 가 펼쳐질 수 있다.
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
                    return None
            except Exception:  # noqa: BLE001
                continue

        # ── (4) F — size-aware poll ─────────────────────────────────────
        # bounding_box.height/width 가 > 0 이 될 때까지 최대 2s. 페이지 로드
        # 직후 menu 가 점진적으로 expand 되는 transition (CSS/JS animation) 케이스.
        try:
            for _ in range(10):  # 200ms x 10 = 2s
                page.wait_for_timeout(200)
                if locator.is_visible():
                    log.info("[Step %s] visibility-healer 복구 — size poll", step_id)
                    return None
        except Exception:  # noqa: BLE001
            pass

        # ── (5) C — 형제 매치 swap ───────────────────────────────────────
        sibling = self._find_visible_sibling(locator, step_id)
        if sibling is not None:
            return sibling

        log.debug(
            "[Step %s] visibility-healer — 모든 전략 무력 (scroll/ancestor/page-hover/size-poll/sibling)",
            step_id,
        )
        return None

    @staticmethod
    def _find_visible_sibling(locator: Locator, step_id) -> Optional[Locator]:
        """Locator 가 다중 매치이고 ``.first`` 가 hidden 일 때 visible 한 형제 swap.

        Playwright 1.36+ 의 ``filter(visible=True)`` 사용. ``.first`` 의 부모
        scope (= 원래 매치 집합) 에서 visible 만 추려 그 첫 element 를 반환.
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

    # ── 스크린샷 ──
    @staticmethod
    def _screenshot(page: Page, artifacts: str, step_id, suffix: str) -> str:
        """스텝 실행 후 스크린샷을 저장하고 파일 경로를 반환한다."""
        path = os.path.join(artifacts, f"step_{step_id}_{suffix}.png")
        page.screenshot(path=path)
        return path

    @staticmethod
    def _screenshot_masked(
        page: Page, artifacts: str, step_id, suffix: str,
        mask: Optional[list] = None,
    ) -> str:
        """``_screenshot`` 의 마스킹 버전 — 지정된 locator 위치를 검정 박스 처리.

        T-D (P0.1 #3) — auth_login 의 email/password/TOTP input 처럼 평문이
        화면에 남는 element 가 PNG 캡처에 그대로 노출되는 것을 방지한다.
        ``mask`` 는 Locator 의 list 또는 None. detached/0건 locator 는 Playwright
        내부에서 no-op 처리되므로 제출 후 navigation 된 페이지에 그대로 넘겨도
        안전하다.
        """
        path = os.path.join(artifacts, f"step_{step_id}_{suffix}.png")
        try:
            page.screenshot(path=path, mask=mask or [])
        except TypeError:
            # 일부 구버전 Playwright 가 mask 인자 미지원 — 안전을 위해 mask 적용
            # 못한 채라도 스크린샷은 남기지 말고 실패 처리 (자격증명 노출 방지).
            log.warning(
                "[Step %s] mask 미지원 Playwright — auth_login 스크린샷 생략 (security)",
                step_id,
            )
            return ""
        return path

    @staticmethod
    def _safe_screenshot(page: Page, path: str):
        """스크린샷을 저장하되, 실패해도 예외를 무시한다."""
        try:
            page.screenshot(path=path)
        except Exception:
            pass
