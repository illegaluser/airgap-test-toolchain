"""Discover URLs (URL 자동 수집) — BFS 크롤러.

Recording UI 의 "Discover URLs" 기능 본체. 시작 URL 한 개와 (선택) auth
storageState 를 받아 같은 호스트 안의 anchor 링크를 BFS 로 따라간다.

커버리지 보강 옵션 (PLAN_URL_DISCOVERY_COVERAGE.md):
- use_sitemap: robots.txt 의 Sitemap: + /sitemap.xml 시드
- capture_requests: page 가 부르는 같은 호스트 GET document/xhr/fetch URL 수집
- spa_selectors: data-href / role=link 등 SPA 신호 셀렉터로 URL 추출
- ignore_query: normalize_url 시 모든 쿼리 문자열 제거 (페이지네이션 통합)
- include_subdomains: 같은 루트 도메인의 서브도메인을 같은 호스트로 취급

호출 규약:
    cfg = DiscoverConfig(seed_url=..., storage_state_path=..., fingerprint_kwargs=...)
    results, abort_reason = discover_urls(cfg, on_progress=cb, cancel_event=ev)

`normalize_url()` 은 visited-set 키와 tour-script 입력 검증에서 공유된다.
정규화 정책 변경 시 양쪽 사용처가 같이 영향받는다는 점에 주의.
"""

from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
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
    source: str = "anchor"  # seed | anchor | sitemap | request | spa_selector


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

    # 커버리지 보강 옵션
    use_sitemap: bool = True
    capture_requests: bool = True
    spa_selectors: bool = False
    ignore_query: bool = False
    include_subdomains: bool = False

    # capture_requests 가 따라잡을 resource type. 1차 고정.
    capture_resource_types: tuple[str, ...] = ("document", "xhr", "fetch")
    # spa_selectors 가 ON 일 때 사용할 querySelector 목록. 1차 고정.
    spa_selector_list: tuple[str, ...] = (
        '[role="link"][data-href]',
        'button[data-href]',
        '[data-link-to]',
        '[data-route]',
    )
    # sitemap fetch 가드
    sitemap_max_urls: int = 1000
    sitemap_max_index_followed: int = 50
    sitemap_timeout_sec: float = 5.0


_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(
    raw: str,
    *,
    trash_query_params: tuple[str, ...] = (),
    strip_all_query: bool = False,
) -> str:
    """visited-set 키 + tour-script 매칭에 공유되는 정규화.

    - scheme/host 소문자화, fragment 제거
    - strip_all_query=True 면 모든 쿼리 제거. 아니면 trash_query_params 제거 후 키 정렬
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
    if strip_all_query:
        query = ""
    else:
        trash = {q.lower() for q in trash_query_params}
        kept = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
                if k.lower() not in trash]
        kept.sort()
        query = urlencode(kept)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _host_matches(seed_host: str, candidate_host: str, *,
                  include_subdomains: bool) -> bool:
    """seed 호스트 매칭. include_subdomains=True 면 'a.x' 의 서브도메인도 허용.

    `endswith("." + seed)` 의 점 포함이 'evil-x' 가 'x' 를 잡지 못하게 가드.
    """
    s = (seed_host or "").lower()
    c = (candidate_host or "").lower()
    if not s or not c:
        return False
    if c == s:
        return True
    if include_subdomains and c.endswith("." + s):
        return True
    return False


def _has_excluded_extension(path: str, extensions: tuple[str, ...]) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in extensions)


def _matches_excluded_pattern(url: str, patterns: tuple[str, ...]) -> bool:
    return any(pat in url for pat in patterns)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_xml_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _fetch_sitemap_seeds(
    page,
    seed_url: str,
    *,
    max_urls: int,
    max_index_followed: int,
    timeout_sec: float,
) -> list[str]:
    """robots.txt + /sitemap.xml 을 best-effort 로 읽어 URL 후보 반환.

    실패는 조용히 빈 리스트. 외부 도메인 sitemap 은 무시.
    같은 호스트/포트 검증은 호출자(BFS) 에 위임 — 여기서는 raw URL 만 수집.
    robots.txt 의 Disallow 는 1차 미준수 (PLAN_URL_DISCOVERY_COVERAGE.md).
    """
    seed = urlparse(seed_url)
    if not seed.scheme or not seed.netloc:
        return []
    origin = f"{seed.scheme}://{seed.netloc}"
    timeout_ms = int(timeout_sec * 1000)

    sitemap_candidates: list[str] = []
    try:
        resp = page.context.request.get(f"{origin}/robots.txt", timeout=timeout_ms)
        if resp.ok:
            for line in resp.text().splitlines():
                s = line.strip()
                if s.lower().startswith("sitemap:"):
                    sm = s.split(":", 1)[1].strip()
                    if sm:
                        sitemap_candidates.append(sm)
    except Exception:
        pass

    if not sitemap_candidates:
        sitemap_candidates.append(f"{origin}/sitemap.xml")

    results: list[str] = []
    seen_sitemaps: set[str] = set()
    indexes_left = max_index_followed

    def _process(sm_url: str) -> None:
        nonlocal indexes_left
        if sm_url in seen_sitemaps or len(results) >= max_urls:
            return
        seen_sitemaps.add(sm_url)
        if sm_url.endswith(".gz"):
            return  # gzip 1차 미지원
        try:
            resp = page.context.request.get(sm_url, timeout=timeout_ms)
        except Exception:
            return
        try:
            ok = resp.ok
        except Exception:
            ok = False
        if not ok:
            return
        try:
            ct = (resp.headers.get("content-type") or "").lower()
        except Exception:
            ct = ""
        if "gzip" in ct:
            return
        try:
            body = resp.body()
        except Exception:
            return
        try:
            root = ET.fromstring(body)
        except Exception:
            return
        local = _local_xml_tag(root.tag)
        if local == "sitemapindex":
            for el in root.iter():
                if _local_xml_tag(el.tag) != "loc" or not el.text:
                    continue
                child = el.text.strip()
                if not child or indexes_left <= 0:
                    continue
                cp = urlparse(child)
                if cp.netloc != seed.netloc:
                    continue  # 외부 도메인 sitemap 은 미참조
                indexes_left -= 1
                _process(child)
                if len(results) >= max_urls:
                    break
        elif local == "urlset":
            for el in root.iter():
                if _local_xml_tag(el.tag) != "loc" or not el.text:
                    continue
                href = el.text.strip()
                if href:
                    results.append(href)
                    if len(results) >= max_urls:
                        break

    for sm in sitemap_candidates:
        if len(results) >= max_urls:
            break
        _process(sm)

    return results


def _extract_spa_hrefs(page, selectors: tuple[str, ...]) -> list[str]:
    """data-href / data-link-to / data-route SPA 신호 셀렉터로 URL 후보 추출.

    상대 URL 은 page.url 기준 absolute 화. onclick 본문 정적 분석은 미지원.
    """
    if not selectors:
        return []
    combined = ", ".join(selectors)
    script = """
    (els) => els.map((e) => {
      const v = e.getAttribute('data-href')
        || e.getAttribute('data-link-to')
        || e.getAttribute('data-route');
      if (!v) return null;
      try { return new URL(v, location.href).href; } catch (_) { return null; }
    }).filter(Boolean)
    """
    try:
        return page.eval_on_selector_all(combined, script)
    except PlaywrightError:
        return []


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
    seed_host = (seed_parsed.hostname or "").lower()
    seed_scheme = (seed_parsed.scheme or "").lower()
    seed_eff_port = seed_parsed.port if seed_parsed.port is not None \
        else _DEFAULT_PORTS.get(seed_scheme)

    def _candidate_passes(parsed) -> bool:
        cand_scheme = (parsed.scheme or "").lower()
        if cand_scheme not in ("http", "https"):
            return False
        if cand_scheme != seed_scheme:
            return False
        cand_host = (parsed.hostname or "").lower()
        if not _host_matches(seed_host, cand_host,
                             include_subdomains=cfg.include_subdomains):
            return False
        cand_eff_port = parsed.port if parsed.port is not None \
            else _DEFAULT_PORTS.get(cand_scheme)
        if cand_eff_port != seed_eff_port:
            return False
        return True

    results: list[DiscoveredUrl] = []
    visited: set[str] = set()
    queue: list[tuple[str, int, str]] = [(cfg.seed_url, 0, "seed")]
    visited.add(normalize_url(
        cfg.seed_url,
        trash_query_params=cfg.trash_query_params,
        strip_all_query=cfg.ignore_query,
    ))
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

        # sitemap 시드 (BFS 진입 전 1회)
        if cfg.use_sitemap:
            try:
                sitemap_urls = _fetch_sitemap_seeds(
                    page, cfg.seed_url,
                    max_urls=cfg.sitemap_max_urls,
                    max_index_followed=cfg.sitemap_max_index_followed,
                    timeout_sec=cfg.sitemap_timeout_sec,
                )
            except Exception:
                sitemap_urls = []
            for u in sitemap_urls:
                if not u or _matches_excluded_pattern(u, cfg.exclude_patterns):
                    continue
                parsed = urlparse(u)
                if not parsed.scheme or not parsed.netloc:
                    continue
                if not _candidate_passes(parsed):
                    continue
                if _has_excluded_extension(parsed.path, cfg.exclude_extensions):
                    continue
                key = normalize_url(
                    u,
                    trash_query_params=cfg.trash_query_params,
                    strip_all_query=cfg.ignore_query,
                )
                if key in visited:
                    continue
                visited.add(key)
                queue.append((u, 1, "sitemap"))

        try:
            is_first = True
            while queue and len(results) < cfg.max_pages:
                if cancel_event is not None and cancel_event.is_set():
                    break

                url, depth, source = queue.pop(0)
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
                captured_request_urls: list[str] = []

                # request 캡처는 페이지 단위로 등록/해제 (출처 추적 + thread-safety).
                # default-arg 바인딩으로 late-binding 회피 (linter S1515).
                _on_request = None
                if cfg.capture_requests:
                    def _on_request(req, _bucket=captured_request_urls,
                                    _types=cfg.capture_resource_types):
                        try:
                            if req.resource_type in _types and req.method == "GET":
                                _bucket.append(req.url)
                        except Exception:
                            pass
                    page.on("request", _on_request)

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
                finally:
                    if _on_request is not None:
                        try:
                            page.remove_listener("request", _on_request)
                        except Exception:
                            pass

                results.append(DiscoveredUrl(
                    url=url,
                    status=status,
                    title=title,
                    depth=depth,
                    found_at=_now_iso(),
                    source=source,
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
                    is_external = not _host_matches(
                        seed_host, final_host,
                        include_subdomains=cfg.include_subdomains,
                    )
                    if is_external:
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

                # anchor + (옵션) request capture + (옵션) spa selectors 합류.
                # 같은 URL 이 두 출처에서 잡혀도 visited 가 첫 출처만 남긴다.
                candidate_pool: list[tuple[str, str]] = [(h, "anchor") for h in hrefs]
                if cfg.capture_requests:
                    candidate_pool.extend(
                        (u, "request") for u in captured_request_urls
                    )
                if cfg.spa_selectors:
                    spa_hrefs = _extract_spa_hrefs(page, cfg.spa_selector_list)
                    candidate_pool.extend(
                        (u, "spa_selector") for u in spa_hrefs
                    )

                for href, src in candidate_pool:
                    if not href:
                        continue
                    if _matches_excluded_pattern(href, cfg.exclude_patterns):
                        continue
                    parsed = urlparse(href)
                    if not parsed.scheme or not parsed.netloc:
                        continue
                    if not _candidate_passes(parsed):
                        continue
                    if _has_excluded_extension(parsed.path, cfg.exclude_extensions):
                        continue
                    key = normalize_url(
                        href,
                        trash_query_params=cfg.trash_query_params,
                        strip_all_query=cfg.ignore_query,
                    )
                    if key in visited:
                        continue
                    visited.add(key)
                    queue.append((href, depth + 1, src))
        finally:
            try:
                context.close()
            finally:
                browser.close()

    return results, abort_reason
