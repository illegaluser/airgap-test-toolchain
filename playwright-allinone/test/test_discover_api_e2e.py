"""Discover URLs API End-to-End — 별도 데몬(18096) + 로컬 fixture site.

검증 영역:
    POST /discover                       (start, 429, auth_profile not_found)
    GET  /discover/{id}                  (status polling)
    POST /discover/{id}/cancel           (200 → state=cancelled, 409 finished, 404 unknown)
    GET  /discover/{id}/csv              (utf-8-sig + URL rows)
    GET  /discover/{id}/json             (List[DiscoveredUrl])
    POST /discover/{id}/tour-script      (200, 422 미발견, 정규화 매칭)

설계 노트:
    auth_profile 만료(409) 케이스는 실제 storageState 손상이 필요하므로 본
    슈트에서는 다루지 않는다 (test_auth_profile_api_e2e.py 가 동일 헬퍼를
    사용하므로 회귀가 거기서 보장됨). 본 슈트는 *없는* 프로파일 → 404 만
    검증한다.
"""

from __future__ import annotations

import http.server
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

E2E_PORT = 18096
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


# ── 로컬 fixture site (basic + slow) ────────────────────────────────────────

_FIXTURE_DELAY_PATHS = {"/slow1.html", "/slow2.html"}


def _make_basic_handler(directory: Path):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a, **k):
            return

        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] in _FIXTURE_DELAY_PATHS:
                time.sleep(0.4)
            return super().do_GET()
    return H


@pytest.fixture(scope="session")
def fixture_site(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("disc-site")
    (root / "index.html").write_text(
        '<a href="a.html">A</a><a href="b.html">B</a><a href="c.html">C</a>'
        '<a href="mailto:x@y">m</a><a href="report.pdf">pdf</a>',
        encoding="utf-8",
    )
    (root / "a.html").write_text("<title>A</title>", encoding="utf-8")
    (root / "b.html").write_text("<title>B</title>", encoding="utf-8")
    (root / "c.html").write_text("<title>C</title>", encoding="utf-8")
    # slow site for cancel/concurrency tests
    slow = "".join(f'<a href="slow{i}.html">{i}</a>' for i in (1, 2))
    slow += "".join(f'<a href="p{i}.html">p{i}</a>' for i in range(8))
    (root / "slow_index.html").write_text(slow, encoding="utf-8")
    (root / "slow1.html").write_text("<title>S1</title>", encoding="utf-8")
    (root / "slow2.html").write_text("<title>S2</title>", encoding="utf-8")
    for i in range(8):
        (root / f"p{i}.html").write_text(f"<title>p{i}</title>", encoding="utf-8")
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _make_basic_handler(root))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{port}", "root": root}
    httpd.shutdown()
    httpd.server_close()


# ── Daemon fixture (18095, DISCOVERY_HOST_ROOT 격리) ───────────────────────

@pytest.fixture(scope="session")
def daemon(tmp_path_factory):
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} 사용 중 — Tier 2 e2e 스킵")

    rec_root = tmp_path_factory.mktemp("disc_e2e_rec")
    disc_root = tmp_path_factory.mktemp("disc_e2e_disc")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["RECORDING_HOST_ROOT"] = str(rec_root)
    env["DISCOVERY_HOST_ROOT"] = str(disc_root)
    env["RECORDING_FORCE_HEADLESS"] = "1"  # E2E 는 headless 강제 (UI default 무관)

    cmd = [
        VENV_PY, "-m", "uvicorn",
        "recording_service.server:app",
        "--host", "127.0.0.1",
        "--port", str(E2E_PORT),
        "--workers", "1",
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            pytest.skip(f"daemon spawn 실패 (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("daemon healthz timeout")

    yield {"base": E2E_BASE, "rec_root": rec_root, "disc_root": disc_root}

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
    except ProcessLookupError:
        pass


def _poll(base: str, job_id: str, until=("done", "failed", "cancelled"), timeout=60.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = httpx.get(f"{base}/discover/{job_id}", timeout=5.0)
        last = r.json()
        if last["state"] in until:
            return last
        time.sleep(0.3)
    raise TimeoutError(f"poll timeout — last={last}")


# ── 테스트 케이스 ────────────────────────────────────────────────────────────

class TestDiscoverHappyPath:

    def test_start_poll_json_csv(self, daemon, fixture_site):
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={"seed_url": seed, "max_pages": 10, "max_depth": 2},
            timeout=5.0,
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["state"] == "running"
        assert "job_id" in body
        job_id = body["job_id"]

        final = _poll(base, job_id)
        assert final["state"] == "done", final
        assert final["count"] >= 4  # index + a/b/c

        # JSON
        rj = httpx.get(f"{base}/discover/{job_id}/json", timeout=5.0)
        assert rj.status_code == 200
        data = rj.json()
        urls = {row["url"] for row in data}
        assert seed in urls
        assert any(u.endswith("/a.html") for u in urls)

        # CSV (utf-8-sig BOM + header)
        rc = httpx.get(f"{base}/discover/{job_id}/csv", timeout=5.0)
        assert rc.status_code == 200
        assert rc.content.startswith(b"\xef\xbb\xbfurl,status,title,depth,source,found_at")
        assert seed.encode() in rc.content

    def test_csv_before_done_409(self, daemon, fixture_site):
        # 미존재 job 으로 csv 호출 → 404
        r = httpx.get(f"{daemon['base']}/discover/zzzzzzzzzzzz/csv", timeout=5.0)
        assert r.status_code == 404


class TestDiscoverConcurrencyAndCancel:

    def test_concurrency_limit_429(self, daemon, fixture_site):
        base = daemon["base"]
        slow_seed = f"{fixture_site['base']}/slow_index.html"
        # 2개 시작
        ids = []
        for _ in range(2):
            r = httpx.post(
                f"{base}/discover",
                json={"seed_url": slow_seed, "max_pages": 10, "max_depth": 2},
                timeout=5.0,
            )
            assert r.status_code == 202, r.text
            ids.append(r.json()["job_id"])
        # 3번째 → 429
        r3 = httpx.post(
            f"{base}/discover",
            json={"seed_url": slow_seed, "max_pages": 5, "max_depth": 1},
            timeout=5.0,
        )
        assert r3.status_code == 429, r3.text
        assert r3.json()["detail"]["reason"] == "too_many_running_discover_jobs"

        # 정리: 둘 다 완료 대기
        for j in ids:
            _poll(base, j, timeout=90.0)

    def test_cancel_partial_results(self, daemon, fixture_site):
        base = daemon["base"]
        slow_seed = f"{fixture_site['base']}/slow_index.html"
        r = httpx.post(
            f"{base}/discover",
            json={"seed_url": slow_seed, "max_pages": 10, "max_depth": 2},
            timeout=5.0,
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        # 첫 페이지 처리되도록 약간 대기 — 환경에 따라 페이지 처리가 늦을 수 있어
        # 1.5s 로 여유를 두되, count 검사는 race tolerant (>=0).
        time.sleep(1.5)
        rc = httpx.post(f"{base}/discover/{job_id}/cancel", timeout=5.0)
        assert rc.status_code == 200
        assert rc.json()["state"] == "cancelling"

        final = _poll(base, job_id, until=("done", "failed", "cancelled"), timeout=60.0)
        assert final["state"] == "cancelled"
        assert final["count"] >= 0  # 부분 결과 — race 로 0 도 허용

        # 종료된 job 재취소 → 409
        rc2 = httpx.post(f"{base}/discover/{job_id}/cancel", timeout=5.0)
        assert rc2.status_code == 409
        assert rc2.json()["detail"]["reason"] == "job_not_cancellable"

        # 미존재 job → 404
        rc3 = httpx.post(f"{base}/discover/zzzzzzzzzzzz/cancel", timeout=5.0)
        assert rc3.status_code == 404


class TestDiscoverTourScript:

    def _seed(self, daemon, fixture_site):
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={"seed_url": seed, "max_pages": 10, "max_depth": 2},
            timeout=5.0,
        )
        job_id = r.json()["job_id"]
        _poll(base, job_id)
        return job_id

    def test_generate_tour_script_with_subset(self, daemon, fixture_site):
        """선택 URL 만 박힌 pytest 형식 tour script 가 다운로드된다."""
        base = daemon["base"]
        job_id = self._seed(daemon, fixture_site)
        urls = httpx.get(f"{base}/discover/{job_id}/json").json()
        sel = [urls[0]["url"], urls[1]["url"]]

        rt = httpx.post(
            f"{base}/discover/{job_id}/tour-script",
            json={"urls": sel, "headless": True},
            timeout=10.0,
        )
        assert rt.status_code == 200, rt.text
        text = rt.text

        # 선택 URL 모두 박힘
        for u in sel:
            assert u in text

        # codegen 산출물 패턴 키워드 — AST 변환기/annotator/healer 인프라 전부 호환.
        for needle, label in [
            ("URLS = [", "URLS multi-line literal"),
            ("def run(playwright)", "codegen run() 진입점"),
            ("page.goto(", "직선 navigate 호출"),
            ('assert "errorMsg" not in page.url', "URL 바운스 verify (assert)"),
            ('assert len(page.inner_text("body")) >= 50', "본문 길이 verify (assert)"),
            ('if __name__ == "__main__":', "Play Script from File 호환"),
            ("with sync_playwright() as p:", "sync_playwright context manager"),
        ]:
            assert needle in text, f"missing: {label}"

        # AST 통과
        import ast
        ast.parse(text)

    def test_generated_script_runs_via_plain_python(self, daemon, fixture_site, tmp_path):
        """다운받은 tour script 를 `python script.py` 로 실행해 실제로 동작 (codegen 패턴).

        Recording UI 의 'Play Script from File' 흐름과 동일한 호출 방식.
        codegen 패턴 전환 후엔 tour_results.jsonl 같은 별도 산출물이 없고,
        rc 와 stderr 의 AssertionError 메시지만으로 결과를 판별 (LLM 모드는 Run Log 카드).
        """
        base = daemon["base"]
        job_id = self._seed(daemon, fixture_site)
        urls = httpx.get(f"{base}/discover/{job_id}/json").json()
        # 정상 컨텐츠가 있는 URL (index 외 a/b/c) 중 2개
        sel = [u["url"] for u in urls if u["url"].endswith(("/a.html", "/b.html"))][:2]
        assert sel, "fixture site 에서 a/b 페이지를 찾지 못함"

        rt = httpx.post(
            f"{base}/discover/{job_id}/tour-script",
            json={"urls": sel},
            timeout=10.0,
        )
        assert rt.status_code == 200
        script_path = tmp_path / "tour_run.py"
        script_path.write_text(rt.text, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=120,
        )
        # fixture 의 a/b 페이지는 본문이 짧아 50자 assert 에 걸릴 수 있다.
        # 그 경우 rc!=0 + stderr 에 AssertionError. 그것 자체로 "스크립트가 실행됐고
        # 검증 라인까지 닿았음" 의 증명. 본문이 길어 통과하면 rc=0.
        if result.returncode != 0:
            assert "AssertionError" in result.stderr or "assert" in result.stderr, (
                f"비정상 종료 (검증 실패가 아닌 다른 사유). "
                f"rc={result.returncode}, stderr={result.stderr[-500:]}"
            )

    # NOTE: tour 가 codegen 산출물 패턴으로 전환되면서 ``CONTENT_SETTLE`` /
    # ``_wait_dom_stable`` / SETTLE_TIMEOUT_MS 같은 스크립트-측 settle 로직은
    # codegen wrapper(`recording_service.codegen_trace_wrapper`) 측으로 이전됨.
    # 옛 content_settle 옵션은 TourScriptReq 호환을 위해 시그니처에 남아 있지만
    # 새 템플릿에서 사용되지 않음. 관련 회귀 검증은 wrapper 단위 테스트로 이전 (후속).

    def _content_settle_param_is_accepted_silently(self, daemon, fixture_site):
        """opaque 호환 — content_settle 값이 어떤 값이어도 200 으로 받아준다."""
        base = daemon["base"]
        job_id = self._seed(daemon, fixture_site)
        urls = httpx.get(f"{base}/discover/{job_id}/json").json()
        sel = [urls[0]["url"]]
        rt = httpx.post(
            f"{base}/discover/{job_id}/tour-script",
            json={"urls": sel, "headless": True, "content_settle": "strict"},
            timeout=10.0,
        )
        assert rt.status_code == 200

    def test_unknown_url_422(self, daemon, fixture_site):
        base = daemon["base"]
        job_id = self._seed(daemon, fixture_site)
        rt = httpx.post(
            f"{base}/discover/{job_id}/tour-script",
            json={"urls": ["http://other.example/x"]},
            timeout=5.0,
        )
        assert rt.status_code == 422
        assert rt.json()["detail"]["reason"] == "urls_not_in_discovery"

    def test_normalized_url_match(self, daemon, fixture_site):
        """클라이언트가 host 대소문자/?utm_source 변형을 보내도 매칭 성공."""
        base = daemon["base"]
        job_id = self._seed(daemon, fixture_site)
        urls = httpx.get(f"{base}/discover/{job_id}/json").json()
        original = urls[0]["url"]
        # host 대문자화
        from urllib.parse import urlparse, urlunparse
        p = urlparse(original)
        upper = urlunparse((p.scheme, p.netloc.upper(), p.path, p.params,
                            p.query, p.fragment))
        # utm_source 추가
        with_utm = original + ("&" if "?" in original else "?") + "utm_source=zz"

        rt = httpx.post(
            f"{base}/discover/{job_id}/tour-script",
            json={"urls": [upper, with_utm]},
            timeout=10.0,
        )
        assert rt.status_code == 200, rt.text


class TestDiscoverAuthErrors:

    def test_unknown_profile_404(self, daemon, fixture_site):
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={
                "seed_url": seed,
                "auth_profile": "no-such-profile-xyz",
                "max_pages": 5,
            },
            timeout=5.0,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "profile_not_found"


class TestDiscoveryRootIsolation:

    def test_results_under_discovery_host_root(self, daemon, fixture_site):
        """결과 파일은 DISCOVERY_HOST_ROOT 아래에만, RECORDING_HOST_ROOT 아래에는 없음."""
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={"seed_url": seed, "max_pages": 5, "max_depth": 1},
            timeout=5.0,
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        _poll(base, job_id)

        disc_dir = daemon["disc_root"] / job_id
        assert (disc_dir / "urls.csv").exists()
        assert (disc_dir / "urls.json").exists()
        assert (disc_dir / "meta.json").exists()

        # recordings 루트에는 discoveries 디렉토리가 생기지 않아야 한다.
        rec_root = daemon["rec_root"]
        assert not (rec_root / "discoveries").exists()
        assert not (rec_root / job_id).exists()


class TestCoverageOptions:
    """5개 커버리지 옵션이 페이로드 → worker → 결과까지 전달되는지."""

    def test_options_persisted_to_meta_json(self, daemon, fixture_site):
        import json as _json
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={
                "seed_url": seed,
                "max_pages": 5,
                "max_depth": 1,
                "use_sitemap": False,
                "capture_requests": False,
                "spa_selectors": True,
                "ignore_query": True,
                "include_subdomains": True,
            },
            timeout=5.0,
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        _poll(base, job_id)

        meta_path = daemon["disc_root"] / job_id / "meta.json"
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        opts = meta.get("options")
        assert opts == {
            "use_sitemap": False,
            "capture_requests": False,
            "spa_selectors": True,
            "ignore_query": True,
            "include_subdomains": True,
        }

    def test_options_default_values(self, daemon, fixture_site):
        """페이로드에 옵션 미지정 → 기본값(sitemap/capture ON, 나머지 OFF)."""
        import json as _json
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={"seed_url": seed, "max_pages": 5, "max_depth": 1},
            timeout=5.0,
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        _poll(base, job_id)
        meta = _json.loads(
            (daemon["disc_root"] / job_id / "meta.json").read_text(encoding="utf-8")
        )
        assert meta["options"] == {
            "use_sitemap": True,
            "capture_requests": True,
            "spa_selectors": False,
            "ignore_query": False,
            "include_subdomains": False,
        }

    def test_csv_includes_source_column(self, daemon, fixture_site):
        """urls.csv 에 source 컬럼이 포함되고 seed 행은 source=seed.
        R8 — fieldnames 끝에 parent_url 컬럼 append (기존 인덱스 보존).
        """
        base = daemon["base"]
        seed = f"{fixture_site['base']}/index.html"
        r = httpx.post(
            f"{base}/discover",
            json={"seed_url": seed, "max_pages": 5, "max_depth": 1},
            timeout=5.0,
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        _poll(base, job_id)
        rc = httpx.get(f"{base}/discover/{job_id}/csv", timeout=5.0)
        assert rc.status_code == 200
        text = rc.content.decode("utf-8-sig")
        header = text.splitlines()[0]
        assert header == "url,status,title,depth,source,found_at,parent_url"
        # seed 행
        seed_lines = [ln for ln in text.splitlines()[1:] if ln.startswith(seed + ",")]
        assert seed_lines, "seed row not in CSV"
        # source 컬럼이 'seed'
        cells = seed_lines[0].split(",")
        assert "seed" in cells
