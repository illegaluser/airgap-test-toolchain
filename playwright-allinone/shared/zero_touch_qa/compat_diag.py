"""SUT 호환성 사전 진단.

도메인 URL을 입력받아 페이지의 DOM/런타임 신호를 수집하고
14-DSL(executor.py)로 자동화 가능한지 5종 카테고리로 판정한다.

판정 카테고리:
    compatible              — 14-DSL 커버 범위 안
    limited                 — 일부 동작이 별 트랙 필요 (WebSocket/Dialog/canvas-heavy 등)
    incompatible:closed-shadow — closed Shadow DOM 사용 (자동화 불가)
    incompatible:captcha    — reCAPTCHA/hCaptcha/Turnstile 감지
    unknown                 — 페이지 로드 실패 또는 진단 timeout

Phase 0 §0.5 (PLAN_DSL_COVERAGE 예정 문서) 의 *암묵적 빠짐* 영역 중
closed shadow / WebSocket / Dialog 핸들러 / canvas-heavy 컴포넌트를
addInitScript hook + DOM probe 로 사전 감지한다.
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from playwright.sync_api import sync_playwright

log = logging.getLogger("compat_diag")


# addInitScript — 페이지 로드 *이전* 에 주입돼 closed shadow / WebSocket /
# Dialog 같은 *런타임 호출* 을 모두 가로채 카운트한다. 외부 접근이 불가능한
# closed shadow 는 attachShadow 호출 시점에만 감지 가능하므로 hook 필수.
_INIT_HOOKS = r"""
(() => {
  window.__compatHooks = {
    openShadowCount: 0,
    closedShadowCount: 0,
    websocketCount: 0,
    eventSourceCount: 0,
    dialogCalls: []
  };
  const origAttachShadow = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function(init) {
    try {
      if (init && init.mode === "closed") window.__compatHooks.closedShadowCount++;
      else window.__compatHooks.openShadowCount++;
    } catch (_) {}
    return origAttachShadow.call(this, init);
  };
  const OrigWS = window.WebSocket;
  if (OrigWS) {
    window.WebSocket = function(...args) {
      window.__compatHooks.websocketCount++;
      return new OrigWS(...args);
    };
    window.WebSocket.prototype = OrigWS.prototype;
  }
  const OrigES = window.EventSource;
  if (OrigES) {
    window.EventSource = function(...args) {
      window.__compatHooks.eventSourceCount++;
      return new OrigES(...args);
    };
    window.EventSource.prototype = OrigES.prototype;
  }
  ["alert", "confirm", "prompt"].forEach((name) => {
    const orig = window[name];
    window[name] = function(msg) {
      window.__compatHooks.dialogCalls.push({ type: name, message: String(msg) });
      if (name === "confirm") return true;
      if (name === "prompt") return null;
    };
    window[name].__origRef = orig;
  });
})();
"""


# DOM probe — 페이지 로드 *이후* 에 정적 DOM/전역 상태 조사.
_DOM_PROBE = r"""
() => {
  const iframes = Array.from(document.querySelectorAll("iframe"))
    .map((f) => f.getAttribute("src") || "");
  const scriptSrcs = Array.from(document.querySelectorAll("script"))
    .map((s) => s.src || s.textContent || "")
    .join(" ");
  const captcha = /recaptcha|hcaptcha|turnstile/i.test(scriptSrcs);
  const canvases = Array.from(document.querySelectorAll("canvas"));
  const canvasArea = canvases.reduce((a, c) => a + (c.width * c.height), 0);
  const svgs = Array.from(document.querySelectorAll("svg"));
  const svgArea = svgs.reduce((a, s) => {
    const r = s.getBoundingClientRect();
    return a + Math.max(0, r.width * r.height);
  }, 0);
  const totalArea = Math.max(1, window.innerWidth * window.innerHeight);
  const fw = {
    react: !!(window.React || document.querySelector("[data-reactroot], [data-reactid]")),
    vue: !!(window.Vue || window.__VUE__ || document.querySelector("[data-v-]")),
    angular: !!(window.angular || document.querySelector("[ng-version]")),
    lit: !!(window.LitElement || window.lit),
    svelte: !!document.querySelector(".svelte-"),
    customElementCount: (window.customElements && document.querySelectorAll(":not(:defined)").length === 0)
      ? Array.from(document.querySelectorAll("*")).filter((e) => e.tagName.includes("-")).length
      : 0,
  };
  return { iframes, captcha, canvasArea, svgArea, totalArea, fw };
}
"""


@dataclass
class CompatReport:
    url: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def to_html(self) -> str:
        rows = "".join(
            f"<tr><td>{html.escape(k)}</td><td><pre>{html.escape(json.dumps(v, ensure_ascii=False))}</pre></td></tr>"
            for k, v in self.signals.items()
        )
        reasons = "".join(f"<li>{html.escape(r)}</li>" for r in self.reasons) or "<li>(no flags)</li>"
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Compat report — {html.escape(self.url)}</title>"
            "<style>body{font-family:sans-serif;max-width:900px;margin:2em auto;}"
            "table{border-collapse:collapse;width:100%;}"
            "td,th{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top;}"
            "pre{margin:0;white-space:pre-wrap;word-break:break-all;}"
            ".v{font-size:1.4em;font-weight:bold;}</style></head><body>"
            f"<h1>SUT Compat Report</h1>"
            f"<p>URL: <code>{html.escape(self.url)}</code></p>"
            f"<p class='v'>Verdict: {html.escape(self.verdict)}</p>"
            f"<h2>Reasons</h2><ul>{reasons}</ul>"
            f"<h2>Signals</h2><table><tr><th>key</th><th>value</th></tr>{rows}</table>"
            "</body></html>"
        )


def scan_dom(
    url: str,
    *,
    timeout_ms: int = 30000,
    settle_ms: int = 2000,
    headed: bool = False,
) -> CompatReport:
    """페이지 진입 → addInitScript hook + DOM probe → 판정."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context()
        context.add_init_script(_INIT_HOOKS)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)
            hooks = page.evaluate("() => window.__compatHooks") or {}
            dom = page.evaluate(_DOM_PROBE) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("scan_dom failed for %s: %s", url, exc)
            return CompatReport(
                url=url,
                verdict="unknown",
                reasons=[f"page load failed: {exc}"],
            )
        finally:
            context.close()
            browser.close()

    total_area = dom.get("totalArea") or 1
    signals = {
        "shadow_open": hooks.get("openShadowCount", 0),
        "shadow_closed": hooks.get("closedShadowCount", 0),
        "websocket_count": hooks.get("websocketCount", 0),
        "event_source_count": hooks.get("eventSourceCount", 0),
        "dialog_calls": hooks.get("dialogCalls", []),
        "iframes": dom.get("iframes", []),
        "captcha_detected": bool(dom.get("captcha", False)),
        "canvas_area_ratio": round(dom.get("canvasArea", 0) / total_area, 4),
        "svg_area_ratio": round(dom.get("svgArea", 0) / total_area, 4),
        "frameworks": dom.get("fw", {}),
    }
    return _classify(url, signals)


def _classify(url: str, signals: dict[str, Any]) -> CompatReport:
    if signals["captcha_detected"]:
        return CompatReport(
            url=url,
            verdict="incompatible:captcha",
            reasons=["CAPTCHA script detected (recaptcha/hcaptcha/turnstile)"],
            signals=signals,
        )
    if signals["shadow_closed"] > 0:
        return CompatReport(
            url=url,
            verdict="incompatible:closed-shadow",
            reasons=[f"closed Shadow DOM 사용 (count={signals['shadow_closed']}) — 브라우저 정책상 자동화 불가"],
            signals=signals,
        )

    reasons: list[str] = []
    if signals["websocket_count"] > 0:
        reasons.append(
            f"WebSocket connection {signals['websocket_count']}개 — 14-DSL mock_status/mock_data로 가로채기 불가"
        )
    if signals["event_source_count"] > 0:
        reasons.append(f"EventSource(SSE) {signals['event_source_count']}개 — 동일 사유")
    if signals["canvas_area_ratio"] > 0.3:
        reasons.append(
            f"canvas 면적 비율 {signals['canvas_area_ratio']:.0%} — visual-only 컴포넌트 비중 높음 (selector 추론 불가)"
        )
    if len(signals["dialog_calls"]) > 0:
        reasons.append(
            f"페이지 로드 중 dialog 호출 {len(signals['dialog_calls'])}회 — 14-DSL은 자동 dismiss만 가능"
        )
    if len(signals["iframes"]) > 2:
        reasons.append(
            f"iframe 개수 {len(signals['iframes'])} — 깊게 중첩된 frame chain 안정성 저하 위험"
        )

    verdict = "limited" if reasons else "compatible"
    return CompatReport(url=url, verdict=verdict, reasons=reasons, signals=signals)
