"""replay_proxy 의 auth-profile 자동 매칭 (P4.2~P4.4).

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §5.8

검증:
- metadata.json 에 auth_profile 이 있을 때:
  - run_llm_play  → cmd 에 --storage-state-in 주입 + env 에 PLAYWRIGHT_* 주입
  - run_codegen_replay → env 에 AUTH_STORAGE_STATE_IN + PLAYWRIGHT_* 주입
- metadata.json 에 auth_profile 이 없을 때 — 기존 동작 그대로 (env 미주입)
- verify 실패 시 ReplayAuthExpiredError (P4.4) — UI 가 만료 모달로 분기 가능
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from recording_service import replay_proxy
from recording_service.replay_proxy import (
    PlayResult,
    ReplayAuthExpiredError,
    ReplayProxyError,
    _resolve_auth_for_replay,
    run_codegen_replay,
    run_llm_play,
)


# ─────────────────────────────────────────────────────────────────────────
# fixture
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    return tmp_path


@pytest.fixture
def session_dir(isolated_root: Path) -> Path:
    """녹화 세션 디렉토리 + 빈 metadata.json + scenario.json + original.py."""
    sd = isolated_root / "session-abc"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "metadata.json").write_text("{}", encoding="utf-8")
    (sd / "scenario.json").write_text("[]", encoding="utf-8")
    (sd / "original.py").write_text("# stub\n", encoding="utf-8")
    return sd


def _seed_profile_in_catalog(name: str, isolated_root: Path) -> Path:
    """auth_profiles 카탈로그에 합성 프로파일 등록 + 가짜 storage 파일 생성."""
    from zero_touch_qa.auth_profiles import (
        AuthProfile, FingerprintProfile, NaverProbeSpec, VerifySpec,
        _ensure_root, _upsert_profile,
    )
    _ensure_root()
    storage_p = isolated_root / "auth-profiles" / f"{name}.storage.json"
    storage_p.parent.mkdir(parents=True, exist_ok=True)
    storage_p.write_text(
        json.dumps({"cookies": [{"name": "x", "domain": ".example.com", "value": "v"}],
                    "origins": []}),
        encoding="utf-8",
    )
    prof = AuthProfile(
        name=name,
        service_domain="example.com",
        storage_path=storage_p,
        created_at="2026-04-29T10:00:00+09:00",
        last_verified_at=None,
        ttl_hint_hours=12,
        verify=VerifySpec(
            service_url="https://example.com/mypage",
            service_text="hello",
        ),
        fingerprint=FingerprintProfile(
            viewport_width=1280,
            viewport_height=800,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            color_scheme="light",
            playwright_version="1.57.0",
        ),
        host_machine_id="MAC:test",
        chips_supported=True,
        session_storage_warning=False,
    )
    _upsert_profile(prof)
    return storage_p


def _stub_subprocess_capture(monkeypatch):
    """subprocess 실행 경계를 가로채 cmd/env 를 캡처. 정상 종료 시뮬레이션."""
    captured = {"cmd": None, "env": None, "cwd": None}

    def _stub(cmd, *, cwd, env, timeout_sec, started, log_name="play.log"):
        captured["cmd"] = list(cmd)
        captured["env"] = env
        captured["cwd"] = cwd
        return PlayResult(returncode=0, stdout="", stderr="", elapsed_ms=1.0)

    monkeypatch.setattr(replay_proxy, "_fetch_dify_token_from_container", lambda: None)
    monkeypatch.setattr(replay_proxy, "_run_subprocess", _stub)
    return captured


# ─────────────────────────────────────────────────────────────────────────
# P4.2 — run_llm_play 자동 매칭
# ─────────────────────────────────────────────────────────────────────────

class TestRunLlmPlayAuth:
    def test_no_metadata_no_auth_injection(self, session_dir: Path, monkeypatch):
        """metadata.json 에 auth_profile 키가 없으면 기존 동작."""
        captured = _stub_subprocess_capture(monkeypatch)
        run_llm_play(host_session_dir=str(session_dir), project_root=".")
        assert "--storage-state-in" not in captured["cmd"]
        env = captured["env"] or {}
        assert "AUTH_STORAGE_STATE_IN" not in env
        assert "PLAYWRIGHT_VIEWPORT" not in env

    def test_auth_profile_injects_storage_and_env(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        """auth_profile 메타 + verify 통과 → cmd 에 --storage-state-in + env 주입."""
        storage_p = _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        # verify 통과 모킹.
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {"service_ms": 100}),
        )
        captured = _stub_subprocess_capture(monkeypatch)
        run_llm_play(host_session_dir=str(session_dir), project_root=".")

        cmd = captured["cmd"]
        assert "--storage-state-in" in cmd
        idx = cmd.index("--storage-state-in")
        assert cmd[idx + 1] == str(storage_p)

        env = captured["env"] or {}
        assert env["PLAYWRIGHT_VIEWPORT"] == "1280x800"
        assert env["PLAYWRIGHT_LOCALE"] == "ko-KR"
        assert env["PLAYWRIGHT_TIMEZONE"] == "Asia/Seoul"
        assert env["PLAYWRIGHT_COLOR_SCHEME"] == "light"

    def test_verify_failure_raises_expired(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        """verify 실패 → ReplayAuthExpiredError + profile_name + detail."""
        _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {"fail_reason": "service_text_not_found"}),
        )
        with pytest.raises(ReplayAuthExpiredError) as ei:
            run_llm_play(host_session_dir=str(session_dir), project_root=".")
        assert ei.value.profile_name == "alpha"
        assert ei.value.detail.get("reason") == "verify_failed"
        assert ei.value.detail.get("fail_reason") == "service_text_not_found"

    def test_profile_not_found_raises_expired(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        """metadata 에 적힌 profile 이 카탈로그에 없을 때도 만료로 간주 (UI 가 재시드 유도)."""
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "ghost"}), encoding="utf-8",
        )
        with pytest.raises(ReplayAuthExpiredError) as ei:
            run_llm_play(host_session_dir=str(session_dir), project_root=".")
        assert ei.value.profile_name == "ghost"
        assert ei.value.detail.get("reason") == "profile_not_found"


# ─────────────────────────────────────────────────────────────────────────
# P4.3 — run_codegen_replay 자동 매칭
# ─────────────────────────────────────────────────────────────────────────

class TestRunCodegenReplayAuth:
    def test_no_metadata_no_env_injection(self, session_dir: Path, monkeypatch):
        captured = _stub_subprocess_capture(monkeypatch)
        run_codegen_replay(host_session_dir=str(session_dir))
        # codegen replay 는 wrapper 실행을 위해 기본 env 는 넘기지만,
        # auth metadata 가 없으면 auth-profile 관련 env 는 주입하지 않는다.
        env = captured["env"] or {}
        assert "AUTH_STORAGE_STATE_IN" not in env
        assert "PLAYWRIGHT_VIEWPORT" not in env

    def test_auth_profile_injects_env(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        storage_p = _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {}),
        )
        captured = _stub_subprocess_capture(monkeypatch)
        run_codegen_replay(host_session_dir=str(session_dir))

        env = captured["env"] or {}
        assert env["AUTH_STORAGE_STATE_IN"] == str(storage_p)
        assert env["PLAYWRIGHT_VIEWPORT"] == "1280x800"
        assert env["PLAYWRIGHT_LOCALE"] == "ko-KR"

    def test_codegen_replay_verify_failure_raises_expired(
        self, session_dir: Path, isolated_root: Path, monkeypatch,
    ):
        _seed_profile_in_catalog("alpha", isolated_root)
        (session_dir / "metadata.json").write_text(
            json.dumps({"auth_profile": "alpha"}), encoding="utf-8",
        )
        from zero_touch_qa import auth_profiles as ap
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {"fail_reason": "x"}),
        )
        with pytest.raises(ReplayAuthExpiredError):
            run_codegen_replay(host_session_dir=str(session_dir))


# ─────────────────────────────────────────────────────────────────────────
# P4.4 — ReplayAuthExpiredError 가 ReplayProxyError 의 subclass
# ─────────────────────────────────────────────────────────────────────────

class TestExpiredErrorHierarchy:
    def test_is_replay_proxy_error(self):
        """rplus router 가 ReplayProxyError 로 catch 해도 expired 에러가 잡힘."""
        e = ReplayAuthExpiredError("alpha", {"reason": "x"})
        assert isinstance(e, ReplayProxyError)

    def test_carries_profile_name_and_detail(self):
        e = ReplayAuthExpiredError("booking-via-naver", {"reason": "verify_failed", "service_ms": 100})
        assert e.profile_name == "booking-via-naver"
        assert e.detail["reason"] == "verify_failed"
        assert e.detail["service_ms"] == 100
