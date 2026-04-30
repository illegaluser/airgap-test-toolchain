"""Discover URLs (URL 자동 수집) — BFS 크롤러.

Recording UI 의 "Discover URLs" 기능 본체. 시작 URL 한 개와 (선택) auth
storageState 를 받아 같은 호스트 안의 anchor 링크를 BFS 로 따라간다.
SPA route discovery, hover 메뉴, API 응답 안의 URL 추출은 후속 범위.

호출 규약:
    cfg = DiscoverConfig(seed_url=..., storage_state_path=..., fingerprint_kwargs=...)
    results, abort_reason = discover_urls(cfg, on_progress=cb, cancel_event=ev)

`normalize_url()` 은 visited-set 키와 tour-script 입력 검증에서 공유된다.
정규화 정책 변경 시 양쪽 사용처가 같이 영향받는다는 점에 주의.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qsl, urldefrag, urlencode, urlparse, urlunparse

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


@dataclass
class DiscoveredUrl:
    url: str            # urls.json 에 그대로 박히는 원본 URL (정규화 전 형태)
    status: Optional[int]
    title: Optional[str]
    depth: int
    found_at: str       # ISO 8601 UTC


@dataclass
class DiscoverConfig:
    seed_url: str
    storage_state_path: Optional[Path]
    fingerprint_kwargs: dict
    max_pages: int = 200
    max_depth: int = 3
    request_interval_sec: float = 0.5
    nav_timeout_ms: int = 15000
    wait_until: str = "domcontentloaded"
    settle_timeout_ms: int = 2000
    exclude_patterns: tuple[str, ...] = (
        "/logout", "/signout", "mailto:", "tel:", "javascript:",
    )
    exclude_extensions: tuple[str, ...] = (
        ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif",
        ".svg", ".ico", ".css", ".js", ".woff", ".woff2",
    )
    trash_query_params: tuple[str, ...] = (
        "utm_source", "utm_medium", "utm_campaign", "utm_term",
        "utm_content", "_t", "timestamp", "_",
    )
    auth_drift_window: int = 5
    headless: bool = True


_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(raw: str, *, trash_query_params: tuple[str, ...] = ()) -> str:
    """visited-set 키 + tour-script 매칭에 공유되는 정규화.

    - scheme/host 소문자화, fragment 제거
    - trash_query_params 제거 후 남은 쿼리는 키 정렬
    - 기본 포트(80/443) 제거
    - path 가 빈 경우만 "/" 보정. `/foo` vs `/foo/` 는 별개.
    - http vs https 는 별개 호스트로 본다 (보안 경계).
    """
    raw_no_frag, _ = urldefrag(raw)
    p = urlparse(raw_no_frag)
    scheme = (p.scheme or "").lower()
    host = (p.hostname or "").lower()
    port = p.port
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    netloc = host
    if p.username or p.password:
        userinfo = p.username or ""
        if p.password:
            userinfo += ":" + p.password
        netloc = userinfo + "@" + netloc
    if port:
        netloc += f":{port}"
    path = p.path or "/"
    trash = {q.lower() for q in trash_query_params}
    kept = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in trash]
    kept.sort()
    query = urlencode(kept)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _same_host(seed_netloc: str, candidate_netloc: str) -> bool:
    return seed_netloc.lower() == candidate_netloc.lower()


def _has_excluded_extension(path: str, extensions: tuple[str, ...]) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in extensions)


def _matches_excluded_pattern(url: str, patterns: tuple[str, ...]) -> bool:
    return any(pat in url for pat in patterns)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_urls(
    cfg: DiscoverConfig,
    *,
    on_progress: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[list[DiscoveredUrl], Optional[str]]:
    """BFS 로 같은 호스트의 anchor 링크를 따라가며 URL 을 수집.

    Returns:
        (results, abort_reason). abort_reason 은 정상 종료/사용자 취소 시 None,
        세션 만료 휴리스틱 발동 시 "auth_drift".

        사용자 취소(cancel_event.set())는 abort_reason 이 아니라 호출자가
        cancel_event.is_set() 으로 별도 식별한다.
    """
    seed_parsed = urlparse(cfg.seed_url)
    seed_netloc = seed_parsed.netloc
    seed_host = (seed_parsed.hostname or "").lower()
    seed_scheme = (seed_parsed.scheme or "").lower()

    results: list[DiscoveredUrl] = []
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(cfg.seed_url, 0)]
    visited.add(normalize_url(cfg.seed_url, trash_query_params=cfg.trash_query_params))
    abort_reason: Optional[str] = None
    drift_streak: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.headless)
        ctx_kwargs = dict(cfg.fingerprint_kwargs)
        if cfg.storage_state_path:
            ctx_kwargs["storage_state"] = str(cfg.storage_state_path)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.set_default_navigation_timeout(cfg.nav_timeout_ms)

        try:
            is_first = True
            while queue and len(results) < cfg.max_pages:
                if cancel_event is not None and cancel_event.is_set():
                    break

                url, depth = queue.pop(0)
                if depth > cfg.max_depth:
                    continue

                # 운영 사이트 부담 가드. seed 직전엔 슬립 안 건다.
                if not is_first and cfg.request_interval_sec > 0:
                    time.sleep(cfg.request_interval_sec)
                is_first = False

                status: Optional[int] = None
                title: Optional[str] = None
                final_url = url
                hrefs: list[str] = []

                # per-URL 격리: URL 단위 실패는 status=None 으로 기록하고 계속.
                try:
                    response = page.goto(
                        url,
                        wait_until=cfg.wait_until,
                        timeout=cfg.nav_timeout_ms,
                    )
                    status = response.status if response else None
                    final_url = page.url
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=cfg.settle_timeout_ms
                        )
                    except PlaywrightTimeoutError:
                        pass  # best-effort settle
                    try:
                        title = page.title()
                    except PlaywrightError:
                        title = None
                    try:
                        hrefs = page.eval_on_selector_all(
                            "a[href]", "els => els.map(e => e.href)"
                        )
                    except PlaywrightError:
                        hrefs = []
                except PlaywrightTimeoutError:
                    pass
                except PlaywrightError:
                    pass

                results.append(DiscoveredUrl(
                    url=url,
                    status=status,
                    title=title,
                    depth=depth,
                    found_at=_now_iso(),
                ))
                if on_progress is not None:
                    try:
                        on_progress(len(results), url)
                    except Exception:
                        pass

                # 세션 만료 휴리스틱: 최근 N 응답 final_url host 가 모두 seed 와 다르고
                # 같은 외부 host 로 수렴하면 auth_drift abort.
                final_host = (urlparse(final_url).hostname or "").lower()
                if cfg.auth_drift_window > 0 and final_host:
                    if final_host != seed_host:
                        drift_streak.append(final_host)
                    else:
                        drift_streak.clear()
                    if (
                        len(drift_streak) >= cfg.auth_drift_window
                        and len(set(drift_streak[-cfg.auth_drift_window:])) == 1
                    ):
                        abort_reason = "auth_drift"
                        break

                if depth >= cfg.max_depth:
                    continue

                for href in hrefs:
                    if not href:
                        continue
                    if _matches_excluded_pattern(href, cfg.exclude_patterns):
                        continue
                    parsed = urlparse(href)
                    if not parsed.scheme or not parsed.netloc:
                        continue
                    if (parsed.scheme or "").lower() not in ("http", "https"):
                        continue
                    if not _same_host(seed_netloc, parsed.netloc):
                        continue
                    if (parsed.scheme or "").lower() != seed_scheme:
                        continue
                    if _has_excluded_extension(parsed.path, cfg.exclude_extensions):
                        continue
                    key = normalize_url(href, trash_query_params=cfg.trash_query_params)
                    if key in visited:
                        continue
                    visited.add(key)
                    queue.append((href, depth + 1))
        finally:
            try:
                context.close()
            finally:
                browser.close()

    return results, abort_reason
