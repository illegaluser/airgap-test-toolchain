"""Recording UI Layout E2E — Round 3 + Round 4 변경 회귀 슈트.

본 슈트는 Round 3 (3 토글 메뉴 + 인증 분리 + 세션 일괄 작업) 와 Round 4
(codegen tracing → run-log 모드 탭) 의 *레이아웃·동작 정합성* 을 집중적으로
검증한다. 단위/통합으로 잡히지 않는 영역:

- DOM 순서 (run-log 가 R-PLUS 직후로 이동)
- ``<details>`` 토글의 default 상태 + 클릭 시 펼침/닫힘
- 모드 탭 활성/비활성 (LLM-only / codegen-only / both)
- 인증 책임 분리 (관리 selector vs 녹화 selector)
- CSS cascade 회귀 (.auth-btn / .shot-close 의 흰글자 함정)
- 세션 일괄 선택/삭제 UI 상태 머신 (indeterminate, disabled, count)
- R-PLUS 결과 클립보드 복사

별도 포트(18097) 로 격리 — ``test_recording_ui_e2e.py`` (18093) 와 동시
실행 가능. SID 4종을 디스크 시드로 노출:

- SID_BASIC      : done state, scenario.json 만 — run-log 없음
- SID_LLM_ONLY   : + run_log.jsonl + step PNG (LLM 모드만)
- SID_CODEGEN_ONLY: + codegen_run_log.jsonl + codegen_screenshots/ (codegen 만)
- SID_BOTH       : 두 모드 모두

본 파일은 pre-commit hook (``.githooks/pre-commit``) 의 통합 e2e 슈트에
포함된다.
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
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

E2E_PORT = 18097
E2E_BASE = f"http://127.0.0.1:{E2E_PORT}"

SID_BASIC = "e2eR34BASIC0001"
SID_LLM_ONLY = "e2eR34LLM00002"
SID_CODEGEN_ONLY = "e2eR34CG000003"
SID_BOTH = "e2eR34BOTH00004"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = os.environ.get("E2E_PYTHON", sys.executable)


# 1x1 PNG (8-byte header + 데이터) — 브라우저가 image/png 로 인식.
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xc0\xc0\x00\x00\x00\x05\x00\x01"
    b"\x0d\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _seed_session_done(root: Path, sid: str, *, age_sec: float = 60) -> Path:
    """기본 done 세션 — metadata + scenario.json + original.py."""
    sess = root / sid
    sess.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid,
        "target_url": f"https://{sid.lower()}.test.local/",
        "state": "done",
        "created_at_ts": time.time() - age_sec,
        "step_count": 2,
    }
    (sess / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (sess / "original.py").write_text(
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    b = p.chromium.launch(headless=True)\n"
        "    ctx = b.new_context(); pg = ctx.new_page()\n"
        f"    pg.goto('https://{sid.lower()}.test.local/')\n"
        "    ctx.close(); b.close()\n",
        encoding="utf-8",
    )
    (sess / "scenario.json").write_text(
        json.dumps([
            {"step": 1, "action": "navigate", "target": "",
             "value": f"https://{sid.lower()}.test.local/",
             "description": "navigate", "fallback_targets": []},
            {"step": 2, "action": "click", "target": "role=link, name=A",
             "value": "", "description": "click A", "fallback_targets": []},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    return sess


def _seed_llm_artifacts(sess: Path) -> None:
    """LLM 모드 산출물 — run_log.jsonl + step_<n>_<status>.png."""
    (sess / "run_log.jsonl").write_text(
        '{"step": 1, "action": "navigate", "target": "https://x.test/",'
        ' "status": "PASS", "heal_stage": "none", "ts": 1000.0}\n'
        '{"step": 2, "action": "click", "target": "#btn",'
        ' "status": "HEALED", "heal_stage": "local", "ts": 1005.0}\n'
        '{"step": 3, "action": "click", "target": "#submit",'
        ' "status": "PASS", "heal_stage": "none", "ts": 1010.0}\n',
        encoding="utf-8",
    )
    (sess / "step_1_pass.png").write_bytes(_MINIMAL_PNG)
    (sess / "step_2_healed.png").write_bytes(_MINIMAL_PNG)
    (sess / "step_3_pass.png").write_bytes(_MINIMAL_PNG)


def _seed_codegen_artifacts(sess: Path) -> None:
    """codegen 모드 산출물 — codegen_run_log.jsonl + codegen_screenshots/."""
    (sess / "codegen_run_log.jsonl").write_text(
        '{"step": 1, "action": "goto", "target": "https://x.test/",'
        ' "status": "PASS", "ts": 1000.0, "screenshot": "step_1_pass.png"}\n'
        '{"step": 2, "action": "click", "target": "#missing",'
        ' "status": "FAIL", "ts": 1005.0, "screenshot": "step_2_fail.png",'
        ' "error": "Timeout 30000ms exceeded"}\n',
        encoding="utf-8",
    )
    cg = sess / "codegen_screenshots"
    cg.mkdir(exist_ok=True)
    (cg / "step_1_pass.png").write_bytes(_MINIMAL_PNG)
    (cg / "step_2_fail.png").write_bytes(_MINIMAL_PNG)


@pytest.fixture(scope="session")
def daemon_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e_layout_recordings")
    _seed_session_done(root, SID_BASIC, age_sec=300)
    _seed_llm_artifacts(_seed_session_done(root, SID_LLM_ONLY, age_sec=240))
    _seed_codegen_artifacts(_seed_session_done(root, SID_CODEGEN_ONLY, age_sec=180))
    sess_both = _seed_session_done(root, SID_BOTH, age_sec=120)
    _seed_llm_artifacts(sess_both)
    _seed_codegen_artifacts(sess_both)
    return root


@pytest.fixture(scope="session")
def daemon(daemon_root):
    if _is_port_listening(E2E_PORT):
        pytest.skip(f"port {E2E_PORT} 사용 중")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["RECORDING_HOST_ROOT"] = str(daemon_root)
    env["DISCOVERY_HOST_ROOT"] = str(daemon_root.parent / "layout-disc")
    env["RECORDING_DIFF_ANALYSIS_STUB"] = "1"

    cmd = [
        VENV_PY, "-m", "uvicorn",
        "recording_service.server:app",
        "--host", "127.0.0.1", "--port", str(E2E_PORT),
        "--workers", "1", "--log-level", "warning",
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
            pytest.skip(f"daemon spawn 실패: {stderr[:500]}")
        if _is_port_listening(E2E_PORT):
            break
        time.sleep(0.2)
    else:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: pass
        pytest.skip("daemon 시작 timeout")
    yield E2E_BASE
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: pass
    except Exception:
        pass


@pytest.fixture
def fresh_page(e2e_chromium, daemon) -> Page:
    """페이지 로드 후 details 자동 펼침 *없음* — default 닫힘 상태 검증용."""
    ctx = e2e_chromium.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.goto(f"{daemon}/", wait_until="networkidle")
    yield pg
    ctx.close()


@pytest.fixture
def opened_page(e2e_chromium, daemon) -> Page:
    """모든 <details> 펼친 페이지 — 내부 요소 검증용."""
    ctx = e2e_chromium.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.goto(f"{daemon}/", wait_until="networkidle")
    pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    yield pg
    ctx.close()


def _open_session(page: Page, sid: str) -> None:
    """세션 테이블에서 해당 sid 의 [열기] 클릭 → 결과 패널 노출 대기."""
    btn = page.locator(f'#session-tbody button[data-act="open"][data-sid="{sid}"]')
    btn.wait_for(state="visible", timeout=5_000)
    btn.click()
    page.locator("#result-section").wait_for(state="visible", timeout=5_000)


# ─────────────────────────────────────────────────────────────────────────
# A. 메인 카드 토글 4종 (Round 3 + 4)
# ─────────────────────────────────────────────────────────────────────────

class TestSectionToggleStructure:
    """3 메인 토글 (New Recording / Discover / Login Profile) + Step 추가."""

    TOGGLE_IDS = [
        "#new-recording-section",
        "#discover-section",
        "#login-profile-section",
    ]

    def test_three_main_toggles_present(self, fresh_page: Page):
        for sel in self.TOGGLE_IDS:
            expect(fresh_page.locator(sel)).to_have_count(1)

    def test_three_main_toggles_default_closed(self, fresh_page: Page):
        for sel in self.TOGGLE_IDS:
            assert fresh_page.locator(sel).get_attribute("open") is None, \
                f"{sel} 가 기본적으로 열린 상태 — Round 3 정책 위반"

    def test_clicking_summary_opens_details(self, fresh_page: Page):
        for sel in self.TOGGLE_IDS:
            fresh_page.locator(f"{sel} > summary").click()
            assert fresh_page.locator(sel).get_attribute("open") is not None, \
                f"{sel} 가 클릭 후 열리지 않음"

    def test_clicking_summary_again_closes(self, fresh_page: Page):
        sel = "#new-recording-section"
        sm = fresh_page.locator(f"{sel} > summary")
        sm.click()
        sm.click()
        assert fresh_page.locator(sel).get_attribute("open") is None

    def test_toggles_are_independent(self, fresh_page: Page):
        """하나 열어도 다른 토글은 그대로 닫힘 (accordion 아님)."""
        fresh_page.locator("#discover-section > summary").click()
        assert fresh_page.locator("#discover-section").get_attribute("open") is not None
        assert fresh_page.locator("#new-recording-section").get_attribute("open") is None
        assert fresh_page.locator("#login-profile-section").get_attribute("open") is None

    def test_new_recording_renamed_to_english(self, fresh_page: Page):
        """제목이 'Recording & Play' (영문) — 녹화 시작 + .py 업로드 두 진입의 통합 라벨."""
        text = fresh_page.locator("#new-recording-section > summary").inner_text()
        assert "Recording & Play" in text
        # 옛 명칭이 새지 않아야
        assert "새 녹화 시작" not in text
        assert "New Recording" not in text

    def test_step_add_section_uses_details_toggle(self, opened_page: Page):
        """Round 4 — Step 추가 카드 안에 details 토글 존재."""
        _open_session(opened_page, SID_BASIC)
        # assertion-toggle 자체가 존재
        expect(opened_page.locator("#assertion-toggle")).to_have_count(1)

    def test_step_add_default_collapsed(self, fresh_page: Page):
        """Step 추가 카드는 done 진입 후에도 default 닫힘 (다른 토글과 동일 정책)."""
        _open_session(fresh_page, SID_BASIC)
        # assertion-section 은 노출되지만 안의 details 는 닫혀있어야
        section = fresh_page.locator("#assertion-section")
        expect(section).to_be_visible()
        toggle = fresh_page.locator("#assertion-toggle")
        assert toggle.get_attribute("open") is None


# ─────────────────────────────────────────────────────────────────────────
# B. 인증 책임 분리 (Round 3)
# ─────────────────────────────────────────────────────────────────────────

class TestAuthOwnershipSeparation:
    """관리(Login Profile Registration) 와 녹화(New Recording) 의 selector 분리."""

    def test_recording_form_has_simple_dropdown_only(self, opened_page: Page):
        """New Recording 안의 auth UI 는 #recording-auth-profile dropdown 뿐."""
        recording = opened_page.locator("#new-recording-section")
        # 녹화 폼의 selector 존재
        expect(recording.locator("#recording-auth-profile")).to_have_count(1)
        # 관리 selector 는 녹화 섹션 안에 *없어야*
        assert recording.locator("#auth-profile-select").count() == 0
        # 관리 버튼들도 녹화 섹션 안에 *없어야*
        assert recording.locator("#btn-auth-verify").count() == 0
        assert recording.locator("#btn-auth-delete").count() == 0
        assert recording.locator("#btn-auth-seed").count() == 0

    def test_login_profile_section_owns_management_buttons(self, opened_page: Page):
        """verify/delete/seed 버튼은 모두 Login Profile Registration 섹션 안에."""
        section = opened_page.locator("#login-profile-section")
        for sel in ["#auth-profile-select", "#btn-auth-verify",
                    "#btn-auth-delete", "#btn-auth-seed"]:
            assert section.locator(sel).count() == 1, f"{sel} 가 login-profile-section 안에 없음"

    def test_login_profile_section_owns_all_dialogs(self, opened_page: Page):
        """4개의 인증 dialog 도 같은 섹션 안에."""
        section = opened_page.locator("#login-profile-section")
        for sel in ["#auth-seed-dialog", "#auth-seed-progress",
                    "#auth-expired-dialog", "#auth-machine-mismatch-dialog"]:
            assert section.locator(sel).count() == 1, f"{sel} 가 login-profile-section 안에 없음"

    def test_seed_button_text_is_visible(self, opened_page: Page):
        """R3-4 회귀 가드 — `+ 새 세션 시드` 텍스트가 흰글자/흰배경이 아닌지."""
        btn = opened_page.locator("#btn-auth-seed")
        expect(btn).to_be_visible()
        # textContent 비어있지 않음
        text = btn.inner_text().strip()
        assert text and "시드" in text, f"버튼 텍스트가 비어있거나 잘못됨: {text!r}"
        # 컴퓨티드 color 가 #fff 가 아닌지 — cascade 함정 회귀 가드
        color = btn.evaluate("el => getComputedStyle(el).color")
        # rgb(255, 255, 255) 또는 white / #fff 면 invisible
        assert color not in ("rgb(255, 255, 255)", "rgba(255, 255, 255, 1)", "white", "#fff"), \
            f"`+ 새 세션 시드` 버튼이 흰글자 — cascade 함정 재발: {color}"

    def test_recording_dropdown_populated_from_load_auth_profiles(self, opened_page: Page):
        """페이지 로드 후 #recording-auth-profile 에 최소 1개 옵션 (없음 비로그인) 이 있어야."""
        opts = opened_page.locator("#recording-auth-profile option").count()
        assert opts >= 1, "recording-auth-profile 에 옵션이 없음"
        # default 옵션이 빈 값
        default = opened_page.locator("#recording-auth-profile option").first.get_attribute("value")
        assert default == "", f"첫 옵션의 value 가 빈 문자열이 아님: {default!r}"


# ─────────────────────────────────────────────────────────────────────────
# C. 최근 세션 일괄 작업 (Round 3)
# ─────────────────────────────────────────────────────────────────────────

class TestRecentSessionsBulkOps:

    def test_bulk_action_bar_present(self, fresh_page: Page):
        for sel in ["#btn-session-select-all", "#btn-session-select-none",
                    "#btn-session-delete-selected", "#session-selected-count"]:
            expect(fresh_page.locator(sel)).to_have_count(1)

    def test_session_table_has_8_columns(self, fresh_page: Page):
        """체크박스 컬럼 추가로 colspan 7 → 8."""
        headers = fresh_page.locator("#session-table thead th").count()
        assert headers == 8, f"세션 테이블 컬럼 수가 {headers} (기대 8)"

    def test_each_row_has_checkbox(self, fresh_page: Page):
        """4개의 시드 세션 → 각 행에 체크박스 1개씩."""
        rows = fresh_page.locator("#session-tbody tr")
        # 시드 4건 (BASIC / LLM_ONLY / CODEGEN_ONLY / BOTH)
        assert rows.count() >= 4, "시드 세션 4개가 모두 보여야 함"
        for i in range(4):
            row = rows.nth(i)
            # 첫 td 에 체크박스
            cb = row.locator(".session-row-check")
            assert cb.count() == 1, f"행 {i} 에 체크박스 없음"

    def test_delete_selected_disabled_when_none(self, fresh_page: Page):
        del_btn = fresh_page.locator("#btn-session-delete-selected")
        assert del_btn.is_disabled(), "선택 0건 시 삭제 버튼이 활성"

    def test_select_all_button_checks_all_visible(self, fresh_page: Page):
        fresh_page.locator("#btn-session-select-all").click()
        all_cbs = fresh_page.locator(".session-row-check")
        n = all_cbs.count()
        assert n >= 4
        for i in range(n):
            assert all_cbs.nth(i).is_checked(), f"행 {i} 체크 안 됨"

    def test_select_none_button_unchecks(self, fresh_page: Page):
        fresh_page.locator("#btn-session-select-all").click()
        fresh_page.locator("#btn-session-select-none").click()
        all_cbs = fresh_page.locator(".session-row-check")
        for i in range(all_cbs.count()):
            assert not all_cbs.nth(i).is_checked()

    def test_delete_selected_enabled_after_pick_one(self, fresh_page: Page):
        fresh_page.locator(".session-row-check").first.check()
        fresh_page.wait_for_timeout(50)
        del_btn = fresh_page.locator("#btn-session-delete-selected")
        assert not del_btn.is_disabled()

    def test_selected_count_label_updates(self, fresh_page: Page):
        cb = fresh_page.locator(".session-row-check")
        cb.nth(0).check()
        cb.nth(1).check()
        fresh_page.wait_for_timeout(50)
        count_text = fresh_page.locator("#session-selected-count").inner_text()
        assert "2" in count_text, f"count label = {count_text!r}"

    def test_header_check_indeterminate_on_partial(self, fresh_page: Page):
        """일부만 선택 시 헤더 체크박스가 indeterminate."""
        fresh_page.locator(".session-row-check").nth(0).check()
        fresh_page.wait_for_timeout(50)
        head = fresh_page.locator("#session-th-check")
        is_inter = head.evaluate("el => el.indeterminate")
        assert is_inter, "헤더 체크박스가 indeterminate 가 아님"

    def test_header_check_checked_when_all_selected(self, fresh_page: Page):
        fresh_page.locator("#btn-session-select-all").click()
        fresh_page.wait_for_timeout(50)
        head = fresh_page.locator("#session-th-check")
        assert head.is_checked()
        is_inter = head.evaluate("el => el.indeterminate")
        assert not is_inter

    def test_header_check_toggle_selects_all(self, fresh_page: Page):
        head = fresh_page.locator("#session-th-check")
        head.check()
        fresh_page.wait_for_timeout(50)
        all_cbs = fresh_page.locator(".session-row-check")
        for i in range(all_cbs.count()):
            assert all_cbs.nth(i).is_checked()


# ─────────────────────────────────────────────────────────────────────────
# D. DOM 순서 (Round 4 — run-log 가 R-PLUS 다음으로 이동)
# ─────────────────────────────────────────────────────────────────────────

class TestSectionOrdering:

    def _y(self, page: Page, sel: str) -> float:
        loc = page.locator(sel).first
        loc.wait_for(state="attached", timeout=5_000)
        return loc.evaluate("el => el.getBoundingClientRect().top + window.scrollY")

    def test_run_log_appears_after_rplus(self, opened_page: Page):
        _open_session(opened_page, SID_BOTH)
        rplus_y = self._y(opened_page, "#rplus-section")
        runlog_y = self._y(opened_page, "#run-log-card")
        assert runlog_y > rplus_y, \
            f"run-log-card 가 rplus-section 아래에 있어야 함 (rplus={rplus_y}, runlog={runlog_y})"

    def test_step_add_appears_after_rplus_predecessors(self, opened_page: Page):
        """assertion-section 은 #diff-card 아래 / R-PLUS 위 — 결과 영역 그룹 안."""
        _open_session(opened_page, SID_BOTH)
        diff_y = self._y(opened_page, "#diff-card")
        assertion_y = self._y(opened_page, "#assertion-section")
        rplus_y = self._y(opened_page, "#rplus-section")
        assert diff_y < assertion_y < rplus_y

    def test_three_toggles_in_canonical_order(self, fresh_page: Page):
        """New Recording → Discover URLs → Login Profile Registration."""
        a = self._y(fresh_page, "#new-recording-section")
        b = self._y(fresh_page, "#discover-section")
        c = self._y(fresh_page, "#login-profile-section")
        assert a < b < c


# ─────────────────────────────────────────────────────────────────────────
# E. Run Log 모드 탭 (Round 4)
# ─────────────────────────────────────────────────────────────────────────

class TestRunLogModeTabs:

    def test_mode_tabs_present(self, opened_page: Page):
        _open_session(opened_page, SID_BOTH)
        expect(opened_page.locator("#run-log-mode-tabs")).to_be_visible()
        for mode in ["llm", "codegen"]:
            expect(opened_page.locator(
                f'#run-log-mode-tabs .run-log-mode-tab[data-mode="{mode}"]'
            )).to_have_count(1)

    def test_card_hidden_when_no_run_log_at_all(self, opened_page: Page):
        """SID_BASIC 은 scenario.json 만 — run-log 둘 다 없음 → 카드 hidden."""
        _open_session(opened_page, SID_BASIC)
        # 잠깐 fetch 대기
        opened_page.wait_for_timeout(500)
        card = opened_page.locator("#run-log-card")
        assert card.evaluate("el => el.hidden") is True or \
            not card.is_visible(), "데이터 없는 세션에서 run-log 카드가 보임"

    def test_llm_only_session_disables_codegen_tab(self, opened_page: Page):
        _open_session(opened_page, SID_LLM_ONLY)
        expect(opened_page.locator("#run-log-card")).to_be_visible()
        cg_tab = opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="codegen"]'
        )
        # disabled 또는 active 가 아님
        assert cg_tab.is_disabled(), "LLM-only 세션에서 codegen 탭이 활성"
        # LLM 탭은 active
        llm_tab = opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="llm"]'
        )
        assert "active" in (llm_tab.get_attribute("class") or "")

    def test_codegen_only_session_disables_llm_tab(self, opened_page: Page):
        _open_session(opened_page, SID_CODEGEN_ONLY)
        expect(opened_page.locator("#run-log-card")).to_be_visible()
        llm_tab = opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="llm"]'
        )
        assert llm_tab.is_disabled()
        cg_tab = opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="codegen"]'
        )
        assert "active" in (cg_tab.get_attribute("class") or "")

    def test_both_modes_default_to_llm(self, opened_page: Page):
        _open_session(opened_page, SID_BOTH)
        opened_page.wait_for_timeout(300)
        llm_tab = opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="llm"]'
        )
        cg_tab = opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="codegen"]'
        )
        # 둘 다 enabled
        assert not llm_tab.is_disabled()
        assert not cg_tab.is_disabled()
        # 기본 LLM 활성
        assert "active" in (llm_tab.get_attribute("class") or "")

    def test_codegen_tab_click_renders_codegen_table(self, opened_page: Page):
        _open_session(opened_page, SID_BOTH)
        # 처음엔 LLM 표 — heal_stage=local 행이 있어야
        opened_page.wait_for_selector(".run-log-table tbody tr", timeout=5_000)
        # codegen 탭 클릭
        opened_page.locator(
            '#run-log-mode-tabs .run-log-mode-tab[data-mode="codegen"]'
        ).click()
        opened_page.wait_for_timeout(400)
        rows_cg = opened_page.locator(".run-log-table tbody tr").count()
        # codegen 시드는 2 step
        assert rows_cg == 2, f"codegen 탭 전환 후 표 행 수 {rows_cg} (기대 2)"
        # heal_stage 컬럼은 codegen 에서 모두 'none'
        heal_cells = opened_page.locator(".run-log-table tbody tr .heal-pill").all_inner_texts()
        assert all(h.strip() == "none" for h in heal_cells), \
            f"codegen 표의 heal_stage 가 none 이 아님: {heal_cells}"

    def test_codegen_screenshot_modal_uses_mode_query(self, opened_page: Page):
        """codegen 모드에서 📷 클릭 → src 에 ?mode=codegen 포함."""
        _open_session(opened_page, SID_CODEGEN_ONLY)
        opened_page.wait_for_selector(".shot-link", timeout=5_000)
        opened_page.locator(".shot-link").first.click()
        dlg = opened_page.locator("#shot-dialog")
        expect(dlg).to_have_attribute("open", "")
        src = opened_page.locator("#shot-img").get_attribute("src") or ""
        assert "mode=codegen" in src, f"codegen 모드 src 에 mode 쿼리 누락: {src}"


# ─────────────────────────────────────────────────────────────────────────
# F. R-PLUS 결과 클립보드 복사 (Round 4)
# ─────────────────────────────────────────────────────────────────────────

class TestRPlusClipboardCopy:

    def test_rplus_section_has_copy_button(self, opened_page: Page):
        _open_session(opened_page, SID_BOTH)
        rplus = opened_page.locator("#rplus-section")
        expect(rplus).to_be_visible()
        # 헤더에 copy-btn 존재, target 이 rplus-output
        btn = rplus.locator('.copy-btn[data-copy-target="rplus-output"]')
        expect(btn).to_have_count(1)
        # toast 도 같이
        toast = rplus.locator('.copy-toast[data-toast-for="rplus-output"]')
        expect(toast).to_have_count(1)


# ─────────────────────────────────────────────────────────────────────────
# G. 시각적 회귀 (.shot-close / .auth-btn 의 cascade 함정)
# ─────────────────────────────────────────────────────────────────────────

class TestVisibilityRegressions:
    """Round 3 R3-4 / Round 4 의 button color cascade 함정 회귀 가드."""

    def test_shot_close_button_has_visible_text_color(self, opened_page: Page):
        """스크린샷 모달의 ✕ 버튼이 흰글자/흰배경 invisible 이 아닌지."""
        _open_session(opened_page, SID_LLM_ONLY)
        opened_page.wait_for_selector(".shot-link", timeout=5_000)
        opened_page.locator(".shot-link").first.click()
        close_btn = opened_page.locator(".shot-close")
        expect(close_btn).to_be_visible()
        color = close_btn.evaluate("el => getComputedStyle(el).color")
        assert color not in ("rgb(255, 255, 255)", "rgba(255, 255, 255, 1)", "white", "#fff"), \
            f"✕ 닫기 버튼이 흰글자 — cascade 함정 재발: {color}"

    def test_shot_close_button_has_visible_text(self, opened_page: Page):
        _open_session(opened_page, SID_LLM_ONLY)
        opened_page.wait_for_selector(".shot-link", timeout=5_000)
        opened_page.locator(".shot-link").first.click()
        close_btn = opened_page.locator(".shot-close")
        text = close_btn.inner_text().strip()
        # ✕ 또는 X 또는 close
        assert text, "닫기 버튼에 텍스트가 없음"

    def test_auth_btn_color_not_white(self, opened_page: Page):
        """Login Profile Registration 안의 .auth-btn 류 버튼이 흰글자가 아닌지."""
        # btn-auth-seed 는 항상 enabled — 확실히 보임
        btn = opened_page.locator("#btn-auth-seed")
        color = btn.evaluate("el => getComputedStyle(el).color")
        assert color not in ("rgb(255, 255, 255)", "rgba(255, 255, 255, 1)"), \
            f".auth-btn color cascade 함정 재발: {color}"


# ─────────────────────────────────────────────────────────────────────────
# H. /run-log API 응답 wrap 검증 (Round 4 Breaking)
# ─────────────────────────────────────────────────────────────────────────

class TestRunLogApiContract:
    """endpoint 의 response shape 회귀 — frontend 가 의존하는 형식이 깨지지 않게."""

    def test_run_log_response_is_object_with_mode_and_records(self, daemon):
        import httpx
        r = httpx.get(f"{daemon}/recording/sessions/{SID_LLM_ONLY}/run-log")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict), "응답이 list 가 아니라 {mode, records} dict 여야 함"
        assert data.get("mode") in ("llm", "codegen", "auto")
        assert isinstance(data.get("records"), list)

    def test_run_log_mode_query_codegen(self, daemon):
        import httpx
        r = httpx.get(f"{daemon}/recording/sessions/{SID_BOTH}/run-log?mode=codegen")
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "codegen"
        # codegen 시드는 2 step
        assert len(data["records"]) == 2

    def test_run_log_auto_prefers_llm_when_both(self, daemon):
        import httpx
        r = httpx.get(f"{daemon}/recording/sessions/{SID_BOTH}/run-log")
        assert r.status_code == 200
        assert r.json()["mode"] == "llm"

    def test_run_log_auto_falls_back_codegen(self, daemon):
        import httpx
        r = httpx.get(f"{daemon}/recording/sessions/{SID_CODEGEN_ONLY}/run-log")
        assert r.status_code == 200
        assert r.json()["mode"] == "codegen"

    def test_run_log_404_when_neither(self, daemon):
        import httpx
        r = httpx.get(f"{daemon}/recording/sessions/{SID_BASIC}/run-log")
        assert r.status_code == 404

    def test_screenshot_codegen_mode_serves_jpeg_or_png(self, daemon):
        import httpx
        r = httpx.get(
            f"{daemon}/recording/sessions/{SID_CODEGEN_ONLY}/screenshot/step_1_pass.png"
            "?mode=codegen"
        )
        assert r.status_code == 200
        assert r.headers["content-type"] in ("image/png", "image/jpeg")
