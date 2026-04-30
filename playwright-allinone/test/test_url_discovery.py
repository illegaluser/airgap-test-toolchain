"""url_discovery 단위 테스트 — normalize_url + BFS + 격리/취소/세션만료.

외부 사이트 미참조. 로컬 ``http.server`` fixture HTML 을 띄워 BFS 동작 확인.
인증 없이 동작 (storageState/fingerprint=None). 실제 SSO 사이트는 수동 검증.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from zero_touch_qa.url_discovery import (
    DiscoverConfig,
    _host_matches,
    discover_urls,
    normalize_url,
)


# ── normalize_url 회귀 케이스 ───────────────────────────────────────────────

TRASH = ("utm_source", "utm_medium", "utm_campaign", "_t", "timestamp")


@pytest.mark.parametrize("a,b", [
    # 쿼리 키 순서 무관
    ("http://x/a?a=1&b=2", "http://x/a?b=2&a=1"),
    # host 대소문자 무관
    ("HTTP://Example.com/a", "http://example.com/a"),
    # 기본 포트 제거 (http=80, https=443)
    ("http://x:80/p", "http://x/p"),
    ("https://x:443/p", "https://x/p"),
    # fragment 제거
    ("http://x/p#a", "http://x/p"),
    # trash query param 제거
    ("http://x/p?utm_source=q", "http://x/p"),
])
def test_normalize_equiv(a, b):
    assert normalize_url(a, trash_query_params=TRASH) == \
        normalize_url(b, trash_query_params=TRASH)


@pytest.mark.parametrize("a,b", [
    # trailing slash 정책: /foo 와 /foo/ 는 별개
    ("http://x/foo", "http://x/foo/"),
    # http 와 https 는 별개 호스트
    ("http://x/p", "https://x/p"),
    # path 가 다르면 당연히 별개
    ("http://x/a", "http://x/b"),
])
def test_normalize_distinct(a, b):
    assert normalize_url(a, trash_query_params=TRASH) != \
        normalize_url(b, trash_query_params=TRASH)


def test_normalize_path_default_slash():
    # path 가 빈 경우 "/" 보정
    assert normalize_url("http://x", trash_query_params=()) == "http://x/"


# ── 로컬 HTTP fixture ───────────────────────────────────────────────────────

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # noqa: D401
        return


@contextmanager
def _serve(directory: Path) -> Iterator[int]:
    """`directory` 를 루트로 한 백그라운드 HTTP 서버. 포트 반환."""

    class _Handler(_QuietHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def site_root(tmp_path: Path) -> Path:
    """5페이지 사이트:
    index → a, b, c (depth 1)
        a → d (depth 2)
        c → e (depth 2)
    plus mailto:/.pdf 외부 링크 노이즈.
    """
    (tmp_path / "index.html").write_text(
        '<a href="a.html">A</a>'
        '<a href="b.html">B</a>'
        '<a href="c.html">C</a>'
        '<a href="mailto:x@y.com">mail</a>'
        '<a href="report.pdf">pdf</a>'
        '<a href="https://other.example/x">external</a>',
        encoding="utf-8",
    )
    (tmp_path / "a.html").write_text(
        '<title>A page</title><a href="d.html">D</a>',
        encoding="utf-8",
    )
    (tmp_path / "b.html").write_text(
        "<title>B page</title>no links",
        encoding="utf-8",
    )
    (tmp_path / "c.html").write_text(
        '<title>C page</title><a href="e.html">E</a>',
        encoding="utf-8",
    )
    (tmp_path / "d.html").write_text("<title>D page</title>", encoding="utf-8")
    (tmp_path / "e.html").write_text("<title>E page</title>", encoding="utf-8")
    return tmp_path


def _make_cfg(seed: str, **overrides) -> DiscoverConfig:
    base = dict(
        seed_url=seed,
        storage_state_path=None,
        fingerprint_kwargs={},
        max_pages=20,
        max_depth=3,
        request_interval_sec=0.0,
        settle_timeout_ms=300,
        nav_timeout_ms=5000,
    )
    base.update(overrides)
    return DiscoverConfig(**base)


def test_bfs_discovers_all_pages(site_root: Path):
    """5페이지 사이트 → 6개 URL (index + a/b/c/d/e), depth 0~2."""
    with _serve(site_root) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results, abort = discover_urls(_make_cfg(seed))
    assert abort is None
    urls_by_depth = {r.depth: [] for r in results}
    for r in results:
        urls_by_depth.setdefault(r.depth, []).append(r.url)
    # index + a + b + c + d + e
    assert len(results) == 6
    # external/mailto/.pdf 는 빠진다
    for r in results:
        assert "mailto:" not in r.url
        assert not r.url.endswith(".pdf")
        assert "other.example" not in r.url
    # 깊이: 0=index, 1={a,b,c}, 2={d,e}
    assert urls_by_depth[0] == [seed]
    assert len(urls_by_depth[1]) == 3
    assert len(urls_by_depth[2]) == 2


def test_max_pages_caps_results(site_root: Path):
    with _serve(site_root) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results, _ = discover_urls(_make_cfg(seed, max_pages=2))
    assert len(results) == 2


def test_exclude_pattern_drops_subtree(site_root: Path):
    """exclude_patterns 에 /b 추가 → b 와 그 자손 제외 (b 는 자손 없지만
    a/c/d/e 는 남고 b 만 빠진다)."""
    with _serve(site_root) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        cfg = _make_cfg(
            seed,
            exclude_patterns=("/logout", "mailto:", "tel:", "javascript:", "/b.html"),
        )
        results, _ = discover_urls(cfg)
    urls = [r.url for r in results]
    assert all("b.html" not in u for u in urls)
    assert any("a.html" in u for u in urls)


def test_dedup_via_normalize(tmp_path: Path):
    """utm_source 가 있는 링크와 없는 링크가 동일 페이지로 dedup 된다."""
    (tmp_path / "index.html").write_text(
        '<a href="x.html">x1</a>'
        '<a href="x.html?utm_source=q">x2</a>'
        '<a href="x.html?_t=1">x3</a>',
        encoding="utf-8",
    )
    (tmp_path / "x.html").write_text("<title>X</title>", encoding="utf-8")
    with _serve(tmp_path) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results, _ = discover_urls(_make_cfg(seed))
    # index + x.html — utm_source / _t 변종은 모두 같은 URL 로 묶인다.
    assert len(results) == 2


def test_per_url_isolation(tmp_path: Path):
    """timeout 응답을 주는 한 페이지가 있어도 BFS 가 멈추지 않는다."""
    bomb_event = threading.Event()

    class FlakyHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):  # noqa: D401
            return

        def do_GET(self):
            if self.path.startswith("/slow.html"):
                # request handler 는 짧게 sleep 만 (테스트 timeout=400ms 보다 길게).
                time.sleep(2.0)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<title>Slow</title>")
                bomb_event.set()
                return
            return super().do_GET()

    (tmp_path / "index.html").write_text(
        '<a href="ok1.html">1</a>'
        '<a href="slow.html">slow</a>'
        '<a href="ok2.html">2</a>',
        encoding="utf-8",
    )
    (tmp_path / "ok1.html").write_text("<title>OK1</title>", encoding="utf-8")
    (tmp_path / "ok2.html").write_text("<title>OK2</title>", encoding="utf-8")

    class _Bound(FlakyHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Bound)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        seed = f"http://127.0.0.1:{port}/index.html"
        cfg = _make_cfg(seed, nav_timeout_ms=400, settle_timeout_ms=200)
        results, _ = discover_urls(cfg)
    finally:
        httpd.shutdown()
        httpd.server_close()
    urls = [r.url for r in results]
    assert any("ok1.html" in u for u in urls)
    assert any("ok2.html" in u for u in urls)
    # slow.html 도 결과에 들어가지만 status=None 일 가능성이 높다.
    slow = [r for r in results if "slow.html" in r.url]
    assert slow, "slow URL 결과가 누락됨"
    assert slow[0].status is None or slow[0].status >= 200


def test_cancel_event_partial_results(site_root: Path):
    """첫 URL 처리 직후 cancel → BFS 정상 종료, 부분 결과 보존."""
    cancel = threading.Event()

    def _trigger_after_first(count: int, _last_url: str) -> None:
        if count >= 1:
            cancel.set()

    with _serve(site_root) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results, abort = discover_urls(
            _make_cfg(seed),
            on_progress=_trigger_after_first,
            cancel_event=cancel,
        )
    # 사용자 취소는 abort_reason 이 아님
    assert abort is None
    # 첫 URL 처리 직후 종료 → 부분 결과 1건만 들어가는 게 일반적이지만
    # 진행 콜백 동기화 타이밍에 따라 max_pages 이전에 멈추기만 하면 된다.
    assert 1 <= len(results) < 6


def test_auth_drift_aborts():
    """모든 응답을 다른 호스트(localhost) 로 redirect → auth_drift abort.

    seed 는 127.0.0.1, redirect 는 localhost — 둘 다 127.0.0.1 로 풀리지만
    hostname 문자열이 달라 우리 휴리스틱이 외부 host 로 인식한다.
    """
    # 1) 항상 200 응답을 주는 외부(localhost) 서버
    class OkHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **k):  # noqa: D401
            return

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<title>login</title>")

    ok = socketserver.TCPServer(("127.0.0.1", 0), OkHandler)
    ok_port = ok.server_address[1]
    threading.Thread(target=ok.serve_forever, daemon=True).start()

    redirect_target = f"http://localhost:{ok_port}/login"

    # 2) 모든 응답을 redirect 로 보내는 seed 서버
    class RedirectAll(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **k):  # noqa: D401
            return

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", redirect_target)
            self.end_headers()

    seed_srv = socketserver.TCPServer(("127.0.0.1", 0), RedirectAll)
    seed_port = seed_srv.server_address[1]
    threading.Thread(target=seed_srv.serve_forever, daemon=True).start()

    try:
        seed = f"http://127.0.0.1:{seed_port}/"
        cfg = _make_cfg(seed, auth_drift_window=1, max_pages=5)
        results, abort = discover_urls(cfg)
    finally:
        seed_srv.shutdown()
        seed_srv.server_close()
        ok.shutdown()
        ok.server_close()
    # window=1 이면 첫 redirect 응답에서 즉시 abort.
    assert abort == "auth_drift"
    assert len(results) >= 1


# ── 커버리지 보강 옵션 ──────────────────────────────────────────────────────


def test_host_matches_exact_and_subdomain():
    assert _host_matches("a.example", "a.example", include_subdomains=False)
    assert not _host_matches("a.example", "b.a.example", include_subdomains=False)
    assert _host_matches("a.example", "b.a.example", include_subdomains=True)
    # "evil-a.example" 가 "a.example" 를 잡으면 안 됨 (점 포함 가드)
    assert not _host_matches("a.example", "evil-a.example", include_subdomains=True)
    assert not _host_matches("a.example", "evil.com", include_subdomains=True)
    assert not _host_matches("", "a.example", include_subdomains=True)


def test_normalize_strip_all_query():
    assert normalize_url("http://x/p?a=1&b=2", strip_all_query=True) == "http://x/p"
    # 같은 path 의 다른 쿼리 변종은 strip_all_query 시 같은 키로 dedup
    assert normalize_url("http://x/list?page=1", strip_all_query=True) == \
        normalize_url("http://x/list?page=2", strip_all_query=True)
    # OFF 일 땐 별개
    assert normalize_url("http://x/list?page=1") != normalize_url("http://x/list?page=2")


def test_use_sitemap_seeds_queue(tmp_path: Path):
    """fixture sitemap.xml 의 <urlset><loc> 가 결과에 포함되고 source=sitemap."""
    (tmp_path / "index.html").write_text(
        "<title>Index</title>", encoding="utf-8",
    )
    (tmp_path / "a.html").write_text("<title>A</title>", encoding="utf-8")
    (tmp_path / "b.html").write_text("<title>B</title>", encoding="utf-8")
    with _serve(tmp_path) as port:
        (tmp_path / "sitemap.xml").write_text(
            f'<?xml version="1.0"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>http://127.0.0.1:{port}/a.html</loc></url>\n'
            f'  <url><loc>http://127.0.0.1:{port}/b.html</loc></url>\n'
            f'</urlset>\n',
            encoding="utf-8",
        )
        seed = f"http://127.0.0.1:{port}/index.html"
        cfg_on = _make_cfg(seed, use_sitemap=True)
        results_on, _ = discover_urls(cfg_on)
        cfg_off = _make_cfg(seed, use_sitemap=False)
        results_off, _ = discover_urls(cfg_off)

    sources_on = {r.source for r in results_on}
    assert "sitemap" in sources_on
    assert any(r.source == "sitemap" and r.url.endswith("/a.html") for r in results_on)
    assert any(r.source == "sitemap" and r.url.endswith("/b.html") for r in results_on)
    # OFF 일 땐 sitemap 출처 없음 (anchor 없는 fixture 이므로 index 만 남음)
    assert all(r.source != "sitemap" for r in results_off)
    assert len(results_off) == 1


def test_use_sitemap_via_robots_txt(tmp_path: Path):
    """robots.txt 의 Sitemap: 디렉티브로 sitemap 위치를 알려주는 경로."""
    (tmp_path / "index.html").write_text("<title>I</title>", encoding="utf-8")
    (tmp_path / "x.html").write_text("<title>X</title>", encoding="utf-8")
    with _serve(tmp_path) as port:
        (tmp_path / "robots.txt").write_text(
            f"User-agent: *\nSitemap: http://127.0.0.1:{port}/sm.xml\n",
            encoding="utf-8",
        )
        (tmp_path / "sm.xml").write_text(
            f'<?xml version="1.0"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>http://127.0.0.1:{port}/x.html</loc></url>\n'
            f'</urlset>\n',
            encoding="utf-8",
        )
        seed = f"http://127.0.0.1:{port}/index.html"
        results, _ = discover_urls(_make_cfg(seed, use_sitemap=True))
    assert any(r.source == "sitemap" and r.url.endswith("/x.html") for r in results)


def test_capture_requests_picks_up_fetch(tmp_path: Path):
    """page 가 fetch() 로 부르는 같은 호스트 URL 이 source=request 로 잡힌다."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "x.html").write_text("<title>API</title>", encoding="utf-8")
    (tmp_path / "y.html").write_text("<title>Y</title>", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<a href="y.html">Y</a>'
        '<script>fetch("/api/x.html")</script>',
        encoding="utf-8",
    )
    with _serve(tmp_path) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results_on, _ = discover_urls(_make_cfg(seed, capture_requests=True))
        results_off, _ = discover_urls(_make_cfg(seed, capture_requests=False))

    on_sources = {(r.url.split("?")[0], r.source) for r in results_on}
    assert any(u.endswith("/api/x.html") and s == "request" for u, s in on_sources)
    # OFF 일 땐 anchor 만: index + y.html
    off_urls = [r.url for r in results_off]
    assert any(u.endswith("/y.html") for u in off_urls)
    assert all("/api/x.html" not in u for u in off_urls)


def test_spa_selectors_extract_data_href(tmp_path: Path):
    """data-href / role=link[data-href] 가 ON 일 때 추가 수집된다."""
    (tmp_path / "x.html").write_text("<title>X</title>", encoding="utf-8")
    (tmp_path / "y.html").write_text("<title>Y</title>", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<button data-href="x.html">x</button>'
        '<div role="link" data-href="y.html">y</div>',
        encoding="utf-8",
    )
    with _serve(tmp_path) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results_on, _ = discover_urls(_make_cfg(seed, spa_selectors=True))
        results_off, _ = discover_urls(_make_cfg(seed, spa_selectors=False))

    on_urls = {r.url.rsplit("/", 1)[-1] for r in results_on}
    assert "x.html" in on_urls and "y.html" in on_urls
    assert any(r.source == "spa_selector" for r in results_on)
    # OFF 일 땐 index 만 (anchor 없음)
    off_urls = [r.url for r in results_off]
    assert len(off_urls) == 1


def test_ignore_query_dedups_pagination(tmp_path: Path):
    """?page=1..10 변종이 ignore_query=True 면 1건으로 dedup."""
    (tmp_path / "list.html").write_text("<title>List</title>", encoding="utf-8")
    anchors = "".join(f'<a href="list.html?page={i}">p{i}</a>' for i in range(1, 11))
    (tmp_path / "index.html").write_text(anchors, encoding="utf-8")
    with _serve(tmp_path) as port:
        seed = f"http://127.0.0.1:{port}/index.html"
        results_on, _ = discover_urls(_make_cfg(seed, ignore_query=True))
        results_off, _ = discover_urls(_make_cfg(seed, ignore_query=False))

    list_on = [r for r in results_on if "list.html" in r.url]
    list_off = [r for r in results_off if "list.html" in r.url]
    assert len(list_on) == 1
    assert len(list_off) == 10
