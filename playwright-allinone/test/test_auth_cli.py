"""auth 서브커맨드 CLI (P2) 단위 테스트.

설계: docs/PLAN_AUTH_PROFILE_NAVER_OAUTH.md §9 P2.1~P2.4

검증:
- argparse 트리 (필수 인자, 누락 시 SystemExit)
- list / verify / delete 핸들러 의 정상/실패 rc
- ``--json`` 출력 shape (list / verify)
- seed 핸들러 와이어 (auth_profiles.seed_profile 모킹)
- legacy ``--mode`` 와의 비충돌

실 ``playwright open`` 호출은 모킹 — auth_profiles 단의 seed_profile 자체는
P1 에서 이미 검증되어 있어 본 파일은 *CLI 와이어링* 만 검증한다.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from zero_touch_qa import auth_profiles as ap
from zero_touch_qa.__main__ import (
    _auth_handle_delete,
    _auth_handle_list,
    _auth_handle_seed,
    _auth_handle_verify,
    _build_auth_parser,
    _run_auth_cli,
)
from zero_touch_qa.auth_profiles import (
    AuthProfile,
    FingerprintProfile,
    NaverProbeSpec,
    ProfileNotFoundError,
    SeedVerifyFailedError,
    VerifySpec,
    _upsert_profile,
)


# ─────────────────────────────────────────────────────────────────────────
# fixture
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path))
    return tmp_path


def _make_profile(name: str, root: Path, *, last_verified: str | None = None) -> AuthProfile:
    return AuthProfile(
        name=name,
        service_domain=f"{name}.example.com",
        storage_path=root / f"{name}.storage.json",
        created_at="2026-04-29T17:30:00+09:00",
        last_verified_at=last_verified,
        ttl_hint_hours=12,
        verify=VerifySpec(
            service_url=f"https://{name}.example.com/mypage",
            service_text="hello",
        ),
        fingerprint=FingerprintProfile.default(),
        host_machine_id="MAC:test",
        chips_supported=True,
        session_storage_warning=False,
    )


def _capture(handler, args_ns: SimpleNamespace) -> tuple[int, str]:
    """핸들러 호출 + stdout 캡처."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = handler(args_ns, ap)
    return rc, buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# P2.1 — argparse 트리
# ─────────────────────────────────────────────────────────────────────────

class TestArgparse:
    def test_help_runs(self):
        """--help 출력 시 SystemExit(0)."""
        parser = _build_auth_parser()
        with pytest.raises(SystemExit) as ei:
            parser.parse_args(["--help"])
        assert ei.value.code == 0

    def test_missing_action(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_seed_requires_name(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "seed",
                "--seed-url", "https://x.com/",
                "--verify-service-url", "https://x.com/mypage",
                "--verify-service-text", "hi",
            ])

    def test_seed_full_args(self):
        parser = _build_auth_parser()
        args = parser.parse_args([
            "seed",
            "--name", "alpha",
            "--seed-url", "https://x.com/",
            "--verify-service-url", "https://x.com/mypage",
            "--verify-service-text", "hi",
            "--no-naver-probe",
            "--ttl-hint-hours", "24",
            "--notes", "test",
            "--timeout-sec", "300",
        ])
        assert args.action == "seed"
        assert args.name == "alpha"
        assert args.no_naver_probe is True
        assert args.ttl_hint_hours == 24
        assert args.timeout_sec == 300

    def test_seed_verify_text_optional(self):
        """검증 텍스트를 생략하면 보호 URL 접근만 확인하는 약한 검증 모드."""
        parser = _build_auth_parser()
        args = parser.parse_args([
            "seed",
            "--name", "alpha",
            "--seed-url", "https://x.com/",
            "--verify-service-url", "https://x.com/mypage",
        ])
        assert args.verify_service_text == ""

    def test_list_json_flag(self):
        parser = _build_auth_parser()
        args = parser.parse_args(["list", "--json"])
        assert args.action == "list"
        assert args.as_json is True

    def test_verify_required_name(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["verify"])

    def test_delete_required_name(self):
        parser = _build_auth_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["delete"])


# ─────────────────────────────────────────────────────────────────────────
# P2.3 — list 핸들러
# ─────────────────────────────────────────────────────────────────────────

class TestListHandler:
    def test_empty_human_readable(self, isolated_root: Path):
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=False))
        assert rc == 0
        assert "(no profiles registered)" in out

    def test_empty_json(self, isolated_root: Path):
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=True))
        assert rc == 0
        assert json.loads(out) == []

    def test_populated_human_readable(self, isolated_root: Path):
        _upsert_profile(_make_profile("alpha", isolated_root))
        _upsert_profile(_make_profile("bravo", isolated_root, last_verified="2026-04-29T18:00:00+09:00"))
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=False))
        assert rc == 0
        assert "alpha" in out
        assert "bravo" in out
        assert "2 profiles" in out

    def test_populated_json_shape(self, isolated_root: Path):
        _upsert_profile(_make_profile("alpha", isolated_root, last_verified="2026-04-29T17:35:12+09:00"))
        rc, out = _capture(_auth_handle_list, SimpleNamespace(as_json=True))
        assert rc == 0
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "alpha"
        assert data[0]["service_domain"] == "alpha.example.com"
        assert data[0]["last_verified_at"] == "2026-04-29T17:35:12+09:00"
        assert data[0]["ttl_hint_hours"] == 12
        assert data[0]["chips_supported"] is True


# ─────────────────────────────────────────────────────────────────────────
# P2.3 — verify 핸들러
# ─────────────────────────────────────────────────────────────────────────

class TestVerifyHandler:
    def _args(self, **kwargs) -> SimpleNamespace:
        defaults = {
            "name": "alpha",
            "no_naver_probe": False,
            "timeout_sec": 10,
            "as_json": False,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_missing_profile_returns_1(self, isolated_root: Path):
        rc, _ = _capture(_auth_handle_verify, self._args(name="ghost"))
        assert rc == 1

    def test_pass_through_naver_probe_flag(self, isolated_root: Path, monkeypatch):
        """--no-naver-probe 시 verify_profile 의 naver_probe=False 로 전달."""
        _upsert_profile(_make_profile("alpha", isolated_root))
        captured = {}

        def fake_verify(prof, **kwargs):
            captured.update(kwargs)
            return True, {"service_ms": 100, "naver_probe_ms": None, "naver_ok": None}

        monkeypatch.setattr(ap, "verify_profile", fake_verify)
        rc, _ = _capture(_auth_handle_verify, self._args(no_naver_probe=True, timeout_sec=42))
        assert rc == 0
        assert captured["naver_probe"] is False
        assert captured["timeout_sec"] == 42

    def test_success_human(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {"service_ms": 200, "naver_probe_ms": 150, "naver_ok": True}),
        )
        rc, out = _capture(_auth_handle_verify, self._args())
        assert rc == 0
        assert "✓ OK" in out
        assert "200" in out

    def test_failure_human(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {
                "service_ms": 100, "naver_probe_ms": None, "naver_ok": None,
                "fail_reason": "service_text_not_found",
            }),
        )
        rc, out = _capture(_auth_handle_verify, self._args())
        assert rc == 1
        assert "✗ FAIL" in out
        assert "service_text_not_found" in out

    def test_success_json(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (True, {"service_ms": 200, "naver_probe_ms": 150, "naver_ok": True}),
        )
        rc, out = _capture(_auth_handle_verify, self._args(as_json=True))
        assert rc == 0
        data = json.loads(out)
        assert data["ok"] is True
        assert data["service_ms"] == 200
        assert data["naver_ok"] is True

    def test_failure_json(self, isolated_root: Path, monkeypatch):
        _upsert_profile(_make_profile("alpha", isolated_root))
        monkeypatch.setattr(
            ap, "verify_profile",
            lambda prof, **kw: (False, {"fail_reason": "x"}),
        )
        rc, out = _capture(_auth_handle_verify, self._args(as_json=True))
        assert rc == 1
        data = json.loads(out)
        assert data["ok"] is False
        assert data["fail_reason"] == "x"


# ─────────────────────────────────────────────────────────────────────────
# P2.3 — delete 핸들러
# ─────────────────────────────────────────────────────────────────────────

class TestDeleteHandler:
    def test_missing_returns_1(self, isolated_root: Path):
        rc, _ = _capture(_auth_handle_delete, SimpleNamespace(name="ghost"))
        assert rc == 1

    def test_existing_removed(self, isolated_root: Path):
        prof = _make_profile("alpha", isolated_root)
        _upsert_profile(prof)
        # storage 파일도 만들어두기.
        prof.storage_path.write_text("{}", encoding="utf-8")

        rc, out = _capture(_auth_handle_delete, SimpleNamespace(name="alpha"))
        assert rc == 0
        assert "delete complete" in out
        with pytest.raises(ProfileNotFoundError):
            ap.get_profile("alpha")
        assert not prof.storage_path.exists()


# ─────────────────────────────────────────────────────────────────────────
# P2.2 — seed 핸들러 (auth_profiles.seed_profile 모킹)
# ─────────────────────────────────────────────────────────────────────────

class TestSeedHandler:
    def _args(self, **overrides) -> SimpleNamespace:
        defaults = {
            "name": "booking-via-naver",
            "seed_url": "https://booking.example.com/",
            "verify_service_url": "https://booking.example.com/mypage",
            "verify_service_text": "환영합니다",
            "no_naver_probe": False,
            "service_domain": None,
            "ttl_hint_hours": 12,
            "notes": "",
            "timeout_sec": 600,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_calls_seed_profile_with_verify_spec(self, isolated_root: Path, monkeypatch):
        captured = {}

        def fake_seed(**kwargs):
            captured.update(kwargs)
            return _make_profile("booking-via-naver", isolated_root, last_verified="now")

        monkeypatch.setattr(ap, "seed_profile", fake_seed)
        rc, out = _capture(_auth_handle_seed, self._args(notes="first"))
        assert rc == 0
        assert "✅ seed complete" in out
        assert captured["name"] == "booking-via-naver"
        assert captured["seed_url"] == "https://booking.example.com/"
        assert captured["notes"] == "first"
        # verify spec 의 naver_probe 가 NaverProbeSpec 으로 들어감 (--no-naver-probe 미지정).
        verify_spec = captured["verify"]
        assert verify_spec.service_url == "https://booking.example.com/mypage"
        assert verify_spec.service_text == "환영합니다"
        assert isinstance(verify_spec.naver_probe, NaverProbeSpec)

    def test_no_naver_probe_flag_drops_probe(self, isolated_root: Path, monkeypatch):
        captured = {}

        def fake_seed(**kwargs):
            captured.update(kwargs)
            return _make_profile("alpha", isolated_root)

        monkeypatch.setattr(ap, "seed_profile", fake_seed)
        rc, _ = _capture(_auth_handle_seed, self._args(name="alpha", no_naver_probe=True))
        assert rc == 0
        assert captured["verify"].naver_probe is None

    def test_seed_failure_returns_1(self, isolated_root: Path, monkeypatch):
        def fake_seed(**kwargs):
            raise SeedVerifyFailedError("verify failed: x")

        monkeypatch.setattr(ap, "seed_profile", fake_seed)
        rc, _ = _capture(_auth_handle_seed, self._args())
        assert rc == 1


# ─────────────────────────────────────────────────────────────────────────
# 통합 — _run_auth_cli 진입점 (argparse + dispatch)
# ─────────────────────────────────────────────────────────────────────────

class TestRunAuthCli:
    def test_list_via_run_auth_cli(self, isolated_root: Path):
        rc = _run_auth_cli(["list"])
        assert rc == 0

    def test_unknown_action_argparse_rejects(self, isolated_root: Path):
        with pytest.raises(SystemExit):
            _run_auth_cli(["unknown-action"])


# ─────────────────────────────────────────────────────────────────────────
# 호환성 — 기존 ``--mode`` CLI 가 영향 받지 않는지 (subprocess 검증)
# ─────────────────────────────────────────────────────────────────────────

class TestLegacyModeCompat:
    def test_legacy_mode_execute_still_errors(self, tmp_path: Path):
        """``--mode execute`` (인자 누락) 가 여전히 rc=1 + 기존 에러 메시지."""
        # subprocess 로 실 진입점 호출 — argv routing 확인. sys.executable 로 호출
        # 해서 venv 안의 의존성 (requests 등) 이 보이는 인터프리터 사용.
        result = subprocess.run(
            [sys.executable, "-m", "zero_touch_qa", "--mode", "execute"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "AUTH_PROFILES_DIR": str(tmp_path)},
        )
        assert result.returncode == 1, f"stderr: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "execute mode requires the --scenario argument" in combined

    def test_auth_subcommand_does_not_trigger_mode_arg(self, tmp_path: Path):
        """``auth list`` 호출 시 ``--mode`` required 에러가 나면 안 됨."""
        result = subprocess.run(
            [sys.executable, "-m", "zero_touch_qa", "auth", "list"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "AUTH_PROFILES_DIR": str(tmp_path)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        combined = result.stdout + result.stderr
        # 빈 카탈로그 메시지가 보여야지, --mode 에러가 보이면 안 됨.
        assert "(no profiles registered)" in combined
        assert "--mode" not in combined
