"""popup pages-diff fallback 단위 테스트.

`_run_step_maybe_capture_popup` 의 신규 분기 — `expect_popup` 가 timeout 나도
`active_page.context.pages` diff 로 새 page 가 발견되면 alias 등록.

문서: docs/PLAN_RECORDING_DEDUPE_AND_POPUP_RACE.md
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from zero_touch_qa.config import Config
from zero_touch_qa.executor import QAExecutor, StepResult


def _config(tmp_path) -> Config:
    return Config(
        dify_base_url="http://x",
        dify_api_key="",
        artifacts_dir=str(tmp_path),
        viewport=(800, 600),
        slow_mo=0,
        headed_step_pause_ms=0,
        step_interval_min_ms=0,
        step_interval_max_ms=0,
        heal_threshold=0.8,
        heal_timeout_sec=10,
        scenario_timeout_sec=60,
        dom_snapshot_limit=1000,
    )


class _FakeContext:
    def __init__(self):
        self.pages: list = []


class _FakePage:
    def __init__(self, url: str = "https://example.test/", context: _FakeContext = None):
        self.url = url
        self.context = context or _FakeContext()
        self.context.pages.append(self)

    def wait_for_load_state(self, *_a, **_kw):
        return None

    @contextmanager
    def expect_popup(self, timeout: int = 10000):
        # 항상 timeout — JS dispatch race 케이스 시뮬레이션
        yield MagicMock()
        raise PlaywrightTimeoutError("expect_popup timed out")


def test_popup_pages_diff_fallback_registers_alias_on_timeout(tmp_path):
    """expect_popup 가 timeout 나도 step 실행 직후 새 page 가 생기면 alias 등록."""
    cfg = _config(tmp_path)
    executor = QAExecutor(cfg)

    ctx = _FakeContext()
    active = _FakePage("https://main.test/", ctx)
    pages = {"page": active}

    def _fake_execute_step(self, page, step, resolver, healer, artifacts):
        # JS dispatch fallback 처럼 step 실행 도중에 새 popup page 가 생긴 상황.
        _FakePage("https://popup.test/", ctx)
        return StepResult(
            step_id=step["step"], action=step["action"], target=step["target"],
            value="", description="", status="PASS",
        )

    executor._execute_step = _fake_execute_step.__get__(executor, QAExecutor)

    step = {"step": 3, "action": "click", "target": "x", "page": "page", "popup_to": "page1"}
    result = executor._run_step_maybe_capture_popup(active, pages, step, None, None, str(tmp_path))

    assert result.status == "PASS"
    assert "page1" in pages, "popup pages-diff fallback 으로 alias 가 등록되어야 한다"
    assert pages["page1"].url == "https://popup.test/"


def test_popup_pages_diff_fallback_does_not_re_execute_step(tmp_path):
    """expect_popup timeout 시 step 을 재실행하면 click 두 번 → 팝업 2개.

    회귀 가드 (f52b5964f0fd 케이스). 2026-05-06 — except 분기에서 _execute_step
    을 다시 호출하던 버그로 챗봇 팝업이 2개 떴음.
    """
    cfg = _config(tmp_path)
    executor = QAExecutor(cfg)
    ctx = _FakeContext()
    active = _FakePage("https://main.test/", ctx)
    pages = {"page": active}

    call_count = {"n": 0}

    def _fake_execute_step(self, page, step, resolver, healer, artifacts):
        call_count["n"] += 1
        _FakePage("https://popup.test/", ctx)
        return StepResult(
            step_id=step["step"], action=step["action"], target=step["target"],
            value="", description="", status="PASS",
        )

    executor._execute_step = _fake_execute_step.__get__(executor, QAExecutor)
    step = {"step": 3, "action": "click", "target": "x", "page": "page", "popup_to": "page1"}
    executor._run_step_maybe_capture_popup(active, pages, step, None, None, str(tmp_path))

    assert call_count["n"] == 1, "expect_popup timeout 시 step 재실행 금지"


def test_popup_pages_diff_fallback_skips_when_no_new_page(tmp_path):
    """expect_popup timeout + 새 page 도 없으면 alias 등록 skip (기존 동작 유지)."""
    cfg = _config(tmp_path)
    executor = QAExecutor(cfg)

    ctx = _FakeContext()
    active = _FakePage("https://main.test/", ctx)
    pages = {"page": active}

    def _fake_execute_step(self, page, step, resolver, healer, artifacts):
        return StepResult(
            step_id=step["step"], action=step["action"], target=step["target"],
            value="", description="", status="PASS",
        )

    executor._execute_step = _fake_execute_step.__get__(executor, QAExecutor)

    step = {"step": 3, "action": "click", "target": "x", "page": "page", "popup_to": "page1"}
    result = executor._run_step_maybe_capture_popup(active, pages, step, None, None, str(tmp_path))

    assert result.status == "PASS"
    assert "page1" not in pages
