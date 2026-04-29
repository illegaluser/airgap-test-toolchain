"""auth-profile UI End-to-End (Tier 3).

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §4 + 사용자 e2e 요구

전략:
    별도 포트 (18095) 로 데몬 spawn. **사전에 카탈로그를 디스크에 작성** 해서
    UI 가 드롭다운으로 노출하는지부터 만료 모달 분기까지 헤드리스 Chromium 으로
    검증.

    Tier 2 가 *HTTP API* 를 검증하므로, Tier 3 는 *DOM + 이벤트 핸들러* 의 회귀
    보호에 집중 (마크업 ID / class / 모달 dialog 동작).

검증:
    - 인증 블록 + 모달 4개의 critical ID 존재
    - 사전 등록 프로파일이 드롭다운에 노출 (P5.2)
    - 선택 시 auth-status 갱신
    - ↻ verify 버튼 (P5.3) — 통과 / 실패 분기
    - + 새 세션 시드 → seed-dialog 열림 (P5.4)
    - 취소 → seed-dialog 닫힘
    - 만료된 프로파일로 Start → expiry modal (P5.6)
    - sessionStorage 경고 라벨 노출 (P5.9)
    - 결과 카드 + 세션 테이블 컬럼 (P5.8)
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright


E2E_PORT = 18095
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


# ─────────────────────────────────────────────────────────────────────────
# PATH stub `playwright` (Tier 2 와 동일 패턴)
# ─────────────────────────────────────────────────────────────────────────

_STUB_PLAYWRIGHT = """#!/usr/bin/env bash
case "$1" in
  open)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--save-storage" ]; then
        cat > "$2" <<'EOF'
{"cookies":[
  {"name":"NID_AUT","value":"fake","domain":".naver.com","path":"/","expires":-1,"httpOnly":true,"secure":true,"sameSite":"Lax"},
  {"name":"qa_session","value":"qa","domain":"localhost","path":"/","expires":-1,"httpOnly":false,"secure":false,"sameSite":"Lax"}
],"origins":[]}
EOF
        break
      fi
      shift
    done
    exit 0
    ;;
  codegen)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--output" ]; then
        cat > "$2" <<'EOF'
import os
from playwright.sync_api import sync_playwright
def run(p): pass
with sync_playwright() as p: run(p)
EOF
        break
      fi
      shift
    done
    exit 0
    ;;
  --version)
    echo "Version 1.57.0"
    exit 0
    ;;
esac
exit 0
"""


@pytest.fixture(scope="session")
def stub_path_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("ui_stub_bin")
    p = d / "playwright"
    p.write_text(_STUB_PLAYWRIGHT, encoding="utf-8")
    p.chmod(0o755)
    return d


# ─────────────────────────────────────────────────────────────────────────
# 사전 카탈로그 시드 (HTTP 우회 — 디스크에 직접 작성)
# ─────────────────────────────────────────────────────────────────────────

def _write_storage(path: Path, *, valid: bool = True) -> None:
    """가짜 storage_state. ``valid=False`` 면 만료 시뮬레이션 (쿠키 비움)."""
    if valid:
        data = {
            "cookies": [
                {"name": "NID_AUT", "value": "x", "domain": ".naver.com",
                 "path": "/", "expires": -1, "httpOnly": True, "secure": True,
                 "sameSite": "Lax"},
                {"name": "qa_session", "value": "qa", "domain": "localhost",
                 "path": "/", "expires": -1, "httpOnly": False, "secure": False,
                 "sameSite": "Lax"},
            ],
            "origins": [],
        }
    else:
        data = {"cookies": [], "origins": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)


def _build_index_entry(
    *, name: str, storage_filename: str, machine_id: str, ss_warning: bool = False,
    last_verified_at: str | None = "2026-04-29T17:35:12+09:00",
) -> dict:
    return {
        "name": name,
        "service_domain": "localhost",
        "storage_path": storage_filename,
        "created_at": "2026-04-29T17:30:00+09:00",
        "last_verified_at": last_verified_at,
        "ttl_hint_hours": 12,
        "verify": {
            "service_url": "http://localhost:9999/mypage",  # 검증 안 됨 — Tier 3 는 verify 호출 X
            "service_text": "qa-tester님 환영합니다",
        },
        "fingerprint": {
            "viewport": {"width": 1280, "height": 800},
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "color_scheme": "light",
            "playwright_version": "1.57.0",
            "playwright_channel": "chromium",
            "captured_user_agent": "",
        },
        "host_machine_id": machine_id,
        "chips_supported": True,
        "session_storage_warning": ss_warning,
        "verify_history": [],
        "notes": "Tier 3 e2e fixture",
    }


@pytest.fixture(scope="session")
def seeded_auth_dir(tmp_path_factory):
    """사전에 3개 프로파일을 카탈로그에 등록한 격리 auth-profiles 디렉토리.

    - ``ui-valid``       : 정상, 머신 매치, 검증 OK
    - ``ui-expired``     : storage 비어있음 → 만료 시뮬레이션
    - ``ui-with-ss``     : sessionStorage 경고 라벨 표시용
    """
    d = tmp_path_factory.mktemp("ui_e2e_auth")
    d.chmod(0o700)

    # auth_profiles 의 실제 machine_id 를 fixture 시점에 수집해 정상 프로파일 생성.
    # (subprocess 띄우기 전이라 같은 호스트 → 같은 값)
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from zero_touch_qa.auth_profiles import current_machine_id
        my_machine_id = current_machine_id()
    finally:
        sys.path.pop(0)

    # storage 파일 작성.
    _write_storage(d / "ui-valid.storage.json", valid=True)
    _write_storage(d / "ui-expired.storage.json", valid=False)
    _write_storage(d / "ui-with-ss.storage.json", valid=True)

    # 카탈로그 _index.json 작성.
    index = {
        "version": 1,
        "profiles": [
            _build_index_entry(
                name="ui-valid",
                storage_filename="ui-valid.storage.json",
                machine_id=my_machine_id,
            ),
            _build_index_entry(
                name="ui-expired",
                storage_filename="ui-expired.storage.json",
                machine_id=my_machine_id,
                last_verified_at=None,
            ),
            _build_index_entry(
                name="ui-with-ss",
                storage_filename="ui-with-ss.storage.json",
                machine_id=my_machine_id,
                ss_warning=True,
            ),
        ],
    }
    idx = d / "_index.json"
    idx.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    idx.chmod(0o600)
    return d


# ─────────────────────────────────────────────────────────────────────────
# Daemon fixture (port 18095)
# ─────────────────────────────────────────────────────────────────────────

def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def ui_daemon(stub_path_dir, seeded_auth_dir, tmp_path_factory):
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} 가 이미 사용 중 — Tier 3 e2e 스킵")

    rec_root = tmp_path_factory.mktemp("ui_e2e_rec")

    env = os.environ.copy()
    env["PATH"] = str(stub_path_dir) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["RECORDING_HOST_ROOT"] = str(rec_root)
    env["AUTH_PROFILES_DIR"] = str(seeded_auth_dir)
    env["AUTH_PROFILE_VERIFY_HEADLESS"] = "1"

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
            pytest.skip(f"Tier 3 daemon spawn 실패 (rc={proc.returncode}): {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        pytest.skip("Tier 3 daemon healthz 대기 timeout")

    yield {"base": E2E_BASE, "auth_root": seeded_auth_dir}

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


@pytest.fixture
def page(ui_daemon):
    """헤드리스 Chromium page — 페이지 로드 후 auth-profiles fetch 까지 완료된 상태."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page_ = ctx.new_page()
        page_.goto(ui_daemon["base"] + "/", wait_until="networkidle")
        # 인증 블록 fetch 완료 대기.
        page_.wait_for_function(
            "document.querySelector('#auth-profile-select').options.length > 1",
            timeout=5000,
        )
        yield page_
        ctx.close()
        browser.close()


# ─────────────────────────────────────────────────────────────────────────
# Tests — DOM 마크업
# ─────────────────────────────────────────────────────────────────────────

class TestAuthMarkup:
    def test_critical_ids_present(self, page: Page):
        """인증 블록 + 4 모달의 critical ID 가 DOM 에 존재 (P5.1)."""
        for sel in [
            "#auth-profile-select",
            "#btn-auth-verify",
            "#btn-auth-seed",
            "#auth-status",
            "#auth-seed-dialog",
            "#auth-seed-progress",
            "#auth-expired-dialog",
            "#auth-machine-mismatch-dialog",
        ]:
            assert page.locator(sel).count() == 1, f"missing element: {sel}"

    def test_result_card_has_auth_profile_field(self, page: Page):
        """결과 카드 메타 (P5.8)."""
        assert page.locator("#result-auth-profile").count() == 1

    def test_session_table_has_auth_column(self, page: Page):
        """세션 테이블 7 컬럼 (P5.8)."""
        # auth 컬럼이 4번째 (id, state, target_url, auth, steps, created, actions).
        headers = page.locator("#session-table thead th").all_text_contents()
        assert "auth" in [h.strip().lower() for h in headers]


# ─────────────────────────────────────────────────────────────────────────
# Tests — 드롭다운 + 상태 (P5.2)
# ─────────────────────────────────────────────────────────────────────────

class TestDropdown:
    def test_seeded_profiles_listed(self, page: Page):
        opts = page.locator("#auth-profile-select option").all_text_contents()
        # "(없음 — 비로그인 녹화)" + 3개 시드 프로파일.
        joined = " ".join(opts)
        assert "ui-valid" in joined
        assert "ui-expired" in joined
        assert "ui-with-ss" in joined

    def test_session_storage_warning_label(self, page: Page):
        """sessionStorage 경고 라벨 (P5.9) — 'ui-with-ss' 옵션에 ⚠sessionStorage 표시."""
        opts = page.locator("#auth-profile-select option").all_text_contents()
        ss_opt = next(o for o in opts if "ui-with-ss" in o)
        assert "sessionStorage" in ss_opt or "⚠" in ss_opt

    def test_select_updates_status(self, page: Page):
        page.locator("#auth-profile-select").select_option("ui-valid")
        # 상태 라벨이 갱신될 시간을 잠깐 줌.
        page.wait_for_timeout(200)
        status_text = page.locator("#auth-status").text_content() or ""
        assert "ui-valid" in status_text or "localhost" in status_text

    def test_verify_button_enabled_after_select(self, page: Page):
        """프로파일 선택 전 verify 버튼 disabled, 선택 후 enabled (P5.2)."""
        verify_btn = page.locator("#btn-auth-verify")
        assert verify_btn.is_disabled()
        page.locator("#auth-profile-select").select_option("ui-valid")
        page.wait_for_timeout(200)
        assert not verify_btn.is_disabled()


# ─────────────────────────────────────────────────────────────────────────
# Tests — 시드 모달 (P5.4)
# ─────────────────────────────────────────────────────────────────────────

class TestSeedDialog:
    def test_opens_and_closes(self, page: Page):
        dialog = page.locator("#auth-seed-dialog")
        # 처음엔 닫혀있음.
        assert not dialog.evaluate("el => el.open")
        # 시드 버튼 클릭 → 열림.
        page.locator("#btn-auth-seed").click()
        page.wait_for_timeout(200)
        assert dialog.evaluate("el => el.open")
        # 취소 버튼 (type="button") — required 필드가 비어있어도 즉시 닫힘.
        page.locator("#btn-auth-seed-cancel-input").click()
        page.wait_for_timeout(200)
        assert not dialog.evaluate("el => el.open")

    def test_form_has_required_fields(self, page: Page):
        """시드 입력 폼의 필수 필드 (P5.4). 검증 텍스트는 선택."""
        page.locator("#btn-auth-seed").click()
        page.wait_for_timeout(150)
        form = page.locator("#auth-seed-form")
        # 필수: name, seed_url, verify_service_url.
        for field_name in ["name", "seed_url", "verify_service_url"]:
            inp = form.locator(f"input[name='{field_name}']")
            assert inp.count() == 1, f"missing field: {field_name}"
            assert inp.get_attribute("required") is not None, f"{field_name} not required"
        verify_text = form.locator("input[name='verify_service_text']")
        assert verify_text.count() == 1
        assert verify_text.get_attribute("required") is None
        # 닫기.
        page.locator("#btn-auth-seed-cancel-input").click()

    def test_progress_dialog_explains_close_and_confirm(self, page: Page):
        """진행 모달은 창 닫기 완료 조건과 최종 확인 버튼을 제공한다."""
        progress = page.locator("#auth-seed-progress")
        assert "close the open browser window" in (progress.text_content() or "")
        assert "save the session" in (page.locator("#auth-seed-progress-hint").text_content() or "")
        cancel = page.locator("#btn-auth-seed-cancel")
        assert "Cancel" in (cancel.text_content() or "")
        assert cancel.evaluate("el => getComputedStyle(el).color") != "rgb(255, 255, 255)"
        assert page.locator("#btn-auth-seed-skip").get_attribute("hidden") is not None
        assert page.locator("#btn-auth-seed-done").get_attribute("hidden") is not None


# ─────────────────────────────────────────────────────────────────────────
# Tests — 만료 모달 (P5.6)
# ─────────────────────────────────────────────────────────────────────────

class TestExpiryModal:
    def test_start_with_expired_profile_shows_modal(self, page: Page):
        """만료된 프로파일로 Start Recording → expiry modal."""
        # 1. target_url 입력.
        page.fill("#start-form input[name='target_url']", "http://localhost:9/dummy")
        # 2. 만료된 프로파일 선택.
        page.locator("#auth-profile-select").select_option("ui-expired")
        page.wait_for_timeout(150)
        # 3. Start 클릭. → 백엔드가 verify_profile() 호출 → 실 Playwright 가 9999 포트로
        #    이동 시도 (도달 불가) → service_side_error → 409 → expired modal.
        page.locator("#btn-start").click()

        # 4. expiry modal 이 열릴 때까지 대기 (verify 가 timeout 까지 가니 ~30s).
        expiry_dlg = page.locator("#auth-expired-dialog")
        expect(expiry_dlg).to_be_visible(timeout=45_000)
        # 모달 내용 sanity.
        assert "ui-expired" in (page.locator("#auth-expired-name").text_content() or "")
        # 닫기.
        page.locator("#btn-auth-expired-cancel").click()
        page.wait_for_timeout(200)
        assert not expiry_dlg.evaluate("el => el.open")

    def test_reseed_button_opens_seed_dialog_with_prefill(self, page: Page):
        """expiry modal 의 [재시드] 클릭 → seed-dialog 가 prefill name 으로 열림."""
        page.fill("#start-form input[name='target_url']", "http://localhost:9/dummy")
        page.locator("#auth-profile-select").select_option("ui-expired")
        page.wait_for_timeout(150)
        page.locator("#btn-start").click()
        expect(page.locator("#auth-expired-dialog")).to_be_visible(timeout=45_000)
        page.locator("#btn-auth-expired-reseed").click()
        page.wait_for_timeout(300)
        # seed-dialog 가 열렸고 name 이 prefill 됐는지.
        seed_dlg = page.locator("#auth-seed-dialog")
        assert seed_dlg.evaluate("el => el.open")
        # name 필드: 마지막에 선택된 프로파일 이름이 prefill.
        name_input = page.locator("#auth-seed-form input[name='name']")
        assert name_input.input_value() == "ui-expired"
        # cleanup.
        page.locator("#btn-auth-seed-cancel-input").click()
