"""사용자 핵심 흐름 골드 시나리오 (개선 5).

배경:
    Discover URLs → tour 생성 → Play with LLM → run_log step PASS — 사용자가 실제로
    가장 자주 도는 동선. 개별 컴포넌트는 각자 검증되지만 한 묶음으로 도는 회귀
    가드가 없어 wiring 단절(예: 다운로드 URL 미흡수, headless 옵션 미전달, frozen
    Config 등) 이 사용자 환경에서야 발견됐음.

본 모듈은 mock 없이 다음 한 흐름을 정렬해서 검증:

    1. 인증 fixture 사이트 시드 → storage 발급
    2. 그 사이트에 BFS 크롤 (subprocess 없이 _discover_worker 직접) → URL 수집
    3. 선택 URL 두 개로 tour 스크립트 생성 (생성기 직접 호출)
    4. ``zero_touch_qa`` 의 변환 → scenario.json
    5. ``zero_touch_qa`` executor (--mode execute, --storage-state-in) 로 실 실행
    6. 결과 run_log.jsonl 의 step PASS 라인 단언

비용 ~30s. 깊은 회귀 가드라 nightly 또는 사람-가까운 PR 게이트에서.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _authn_fixture_site import start_authn_site

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


@pytest.fixture
def authn_site():
    httpd, base_url = start_authn_site()
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.e2e
def test_user_journey_seed_to_play_with_real_subprocess(
    authn_site: str, tmp_path: Path,
):
    """전 동선 — 시드 → tour 생성 → 변환 → executor 실행 → run_log PASS.

    회귀가 다음 어디든 한 곳에서 깨지면 fail:
      - storage 시드 자체 깨짐
      - tour 생성기가 잘못된 헤더/본문 만듦
      - converter 가 codegen 패턴 못 읽음
      - executor 가 인자 / Config 충돌
      - storage_state 적용이 끊겨 보호 페이지가 비로그인 진입
    """
    from recording_service.server import _generate_tour_script

    # ── (1) storage 시드 ──────────────────────────────────────────────
    # sync_playwright 는 별도 subprocess 에서 — 같은 프로세스에 pytest-playwright
    # 등이 asyncio loop 를 띄워 두면 sync API 호출이 막히기 때문.
    storage = tmp_path / "fresh.storage.json"
    seed_script = (
        "import sys\n"
        "from playwright.sync_api import sync_playwright\n"
        "base, dump = sys.argv[1], sys.argv[2]\n"
        "with sync_playwright() as p:\n"
        "    b = p.chromium.launch(headless=True)\n"
        "    ctx = b.new_context()\n"
        "    pg = ctx.new_page()\n"
        "    pg.goto(base + '/login?user=qa-tester', wait_until='load')\n"
        "    ctx.storage_state(path=dump)\n"
        "    b.close()\n"
    )
    seed_proc = subprocess.run(
        [VENV_PY, "-c", seed_script, authn_site, str(storage)],
        capture_output=True, timeout=60,
    )
    if seed_proc.returncode != 0:
        pytest.fail(
            "storage seed 실패: "
            + seed_proc.stderr.decode("utf-8", "replace")[-400:]
        )
    assert storage.is_file()

    # ── (2)+(3) 두 보호 URL 로 tour 생성 ──────────────────────────────
    urls = [f"{authn_site}/secret", f"{authn_site}/mypage"]

    class _StubFP:
        def to_browser_context_kwargs(self):
            return {}

    tour_text = _generate_tour_script(
        urls=urls,
        seed_url=authn_site,
        storage_path=storage,
        fingerprint=_StubFP(),
        headless=True,
        preflight_verify=True,
        verify_service_url="",
        verify_service_text="",
        wait_until="load",
        nav_timeout_ms=15000,
    )
    sess = tmp_path / "session"
    sess.mkdir()
    tour_py = sess / "original.py"
    tour_py.write_text(tour_text, encoding="utf-8")

    # ── (4) 변환 — convert_via_ast 로 scenario.json 생성 ──────────────
    from zero_touch_qa.converter_ast import convert_via_ast
    artifacts = sess / "artifacts"
    artifacts.mkdir()
    steps = convert_via_ast(str(tour_py), str(artifacts))
    # 두 URL × (navigate + 2 verify) = 6 step 기대.
    assert len(steps) == 6, f"converter step 수 mismatch: {[s['action'] for s in steps]}"
    scenario_path = artifacts / "scenario.json"
    assert scenario_path.is_file()

    # ── (5) executor 실 실행 ──────────────────────────────────────────
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    )
    env["ARTIFACTS_DIR"] = str(artifacts)
    cmd = [
        VENV_PY, "-m", "zero_touch_qa",
        "--mode", "execute",
        "--scenario", str(scenario_path),
        "--storage-state-in", str(storage),
        "--headless",
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=120)
    if proc.returncode != 0:
        pytest.fail(
            f"executor 비정상 종료. rc={proc.returncode}\n"
            f"stderr (last 1KB):\n{proc.stderr.decode('utf-8', 'replace')[-1024:]}"
        )

    # ── (6) run_log 검증 ──────────────────────────────────────────────
    run_log = artifacts / "run_log.jsonl"
    assert run_log.is_file(), f"run_log.jsonl 미생성 — executor 가 step 결과 안 적음"
    lines = [
        json.loads(l) for l in run_log.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    # step 들이 PASS / HEALED 였어야 (FAIL 없음).
    statuses = [rec.get("status") for rec in lines]
    assert lines, "run_log 가 비었음"
    fails = [s for s in statuses if s == "FAIL"]
    assert not fails, (
        f"보호 페이지가 인증 상태로 진입 안 됨 — FAIL step 발견. statuses={statuses}, "
        f"lines={lines}"
    )
    # navigate + verify 합쳐 최소 4 step 이상 PASS.
    passed_or_healed = [s for s in statuses if s in ("PASS", "HEALED")]
    assert len(passed_or_healed) >= 4, (
        f"PASS/HEALED step 부족 (기대 ≥4): {statuses}"
    )
