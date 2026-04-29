"""executor._apply_fingerprint_env — auth-profile fingerprint env override (P4.1).

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.8 (D10)

검증:
- env 4종 (PLAYWRIGHT_VIEWPORT/_LOCALE/_TIMEZONE/_COLOR_SCHEME) 적용
- env 미설정 시 context_kwargs 미수정
- 잘못된 viewport 형식은 silent skip (경고만)
- UA 관련 env 가 들어가지 않음 (D10)
"""

from __future__ import annotations

import pytest

from zero_touch_qa.executor import _apply_fingerprint_env


class TestApplyFingerprintEnv:
    def test_no_env_no_changes(self, monkeypatch):
        for k in ("PLAYWRIGHT_VIEWPORT", "PLAYWRIGHT_LOCALE",
                  "PLAYWRIGHT_TIMEZONE", "PLAYWRIGHT_COLOR_SCHEME"):
            monkeypatch.delenv(k, raising=False)
        kwargs = {"viewport": {"width": 1280, "height": 800}, "locale": "ko-KR"}
        original = dict(kwargs)
        _apply_fingerprint_env(kwargs)
        assert kwargs == original

    def test_viewport_override(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_VIEWPORT", "1920x1080")
        kwargs = {"viewport": {"width": 1280, "height": 800}}
        _apply_fingerprint_env(kwargs)
        assert kwargs["viewport"] == {"width": 1920, "height": 1080}

    def test_locale_override(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_LOCALE", "en-US")
        kwargs = {"locale": "ko-KR"}
        _apply_fingerprint_env(kwargs)
        assert kwargs["locale"] == "en-US"

    def test_timezone_override(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_TIMEZONE", "America/New_York")
        kwargs = {}
        _apply_fingerprint_env(kwargs)
        assert kwargs["timezone_id"] == "America/New_York"

    def test_color_scheme_override(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_COLOR_SCHEME", "dark")
        kwargs = {}
        _apply_fingerprint_env(kwargs)
        assert kwargs["color_scheme"] == "dark"

    def test_all_four_together(self, monkeypatch):
        monkeypatch.setenv("PLAYWRIGHT_VIEWPORT", "1280x800")
        monkeypatch.setenv("PLAYWRIGHT_LOCALE", "ko-KR")
        monkeypatch.setenv("PLAYWRIGHT_TIMEZONE", "Asia/Seoul")
        monkeypatch.setenv("PLAYWRIGHT_COLOR_SCHEME", "light")
        kwargs = {}
        _apply_fingerprint_env(kwargs)
        assert kwargs == {
            "viewport": {"width": 1280, "height": 800},
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "color_scheme": "light",
        }

    @pytest.mark.parametrize("bad", ["abc", "1280", "1280,800", "x800", "x"])
    def test_invalid_viewport_silent_skip(self, monkeypatch, bad):
        monkeypatch.setenv("PLAYWRIGHT_VIEWPORT", bad)
        kwargs = {"viewport": {"width": 1280, "height": 800}}
        original = dict(kwargs)
        _apply_fingerprint_env(kwargs)
        # 잘못된 값이면 기존 viewport 유지.
        assert kwargs["viewport"] == original["viewport"]

    def test_no_user_agent_env_consumed(self, monkeypatch):
        """D10 — UA 는 임의 spoof 하지 않으므로 어떤 env 도 user_agent 키를 만들지 않는다."""
        monkeypatch.setenv("PLAYWRIGHT_USER_AGENT", "Some/Spoofed/UA")  # 의도적으로 무시되어야 함
        monkeypatch.setenv("PLAYWRIGHT_VIEWPORT", "1280x800")
        kwargs = {}
        _apply_fingerprint_env(kwargs)
        assert "user_agent" not in kwargs
        assert "userAgent" not in kwargs
