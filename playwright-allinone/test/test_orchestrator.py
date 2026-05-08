"""단위 테스트 — replay_service.orchestrator.

실제 Playwright / subprocess / 카탈로그를 monkeypatch 로 격리하고
exit code 분기 + jsonl 이벤트 정확성을 검증.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from recording_service import auth_flow  # noqa: E402
from replay_service import orchestrator  # noqa: E402


# --- helpers -----------------------------------------------------------------


def _make_bundle(tmp_path: Path, alias: str = "packaged", verify_url: str = "https://ex.com/dash") -> Path:
    sess = tmp_path / "sess-src"
    sess.mkdir()
    (sess / "metadata.json").write_text(
        json.dumps({"id": "sess", "auth_profile": "demo"})
    )
    (sess / "original.py").write_text("# noop\n")
    zb = auth_flow.pack_bundle(sess, alias=alias, verify_url=verify_url)
    out = tmp_path / "bundle.zip"
    out.write_bytes(zb)
    return out


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fake_profile(tmp_path: Path):
    fp = MagicMock()
    fp.storage_path = tmp_path / "no-such-storage.json"  # is_file() False → storage 없음
    fp.fingerprint = MagicMock()
    fp.fingerprint.to_env.return_value = {}
    return fp


# --- regex 단위 --------------------------------------------------------------


def test_login_path_pattern_matches():
    p = orchestrator._LOGIN_PATH_PATTERN
    assert p.search("https://x/login")
    assert p.search("https://x/signin?next=/d")
    assert p.search("https://x/auth/sso")
    assert p.search("https://x/SSO/start")
    # 단어 경계 — /loginhint 같은 것은 매칭 안 함.
    assert not p.search("https://x/loginhint")
    assert not p.search("https://x/dashboard")


# --- exit code 분기 ---------------------------------------------------------


def test_run_bundle_unpack_failure_exit_two(tmp_path: Path):
    bogus = tmp_path / "bogus.zip"
    bogus.write_bytes(b"not a zip")
    out = tmp_path / "out"
    rc = orchestrator.run_bundle(bogus, out)
    assert rc == orchestrator.EXIT_SYS_ERROR
    events = _read_jsonl(out / "run_log.jsonl")
    assert any(e.get("event") == "system_error" for e in events)
    assert (out / "exit_code").read_text() == "2"


def test_run_bundle_alias_missing_in_catalog_exit_three(
    tmp_path: Path, monkeypatch
):
    """카탈로그에 alias 가 없으면 exit 3 (auth_seed_expired)."""
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    bundle = _make_bundle(tmp_path)
    out = tmp_path / "out"
    rc = orchestrator.run_bundle(bundle, out)
    assert rc == orchestrator.EXIT_SEED_EXPIRED
    events = _read_jsonl(out / "run_log.jsonl")
    assert any(e.get("event") == "auth_seed_expired" for e in events)


def test_run_bundle_probe_expired_exit_three(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    bundle = _make_bundle(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(
        orchestrator.auth_profiles,
        "get_profile",
        lambda name: _fake_profile(tmp_path),
    )
    monkeypatch.setattr(orchestrator, "probe_verify_url", lambda url, sp: "expired")
    rc = orchestrator.run_bundle(bundle, out)
    assert rc == orchestrator.EXIT_SEED_EXPIRED
    events = _read_jsonl(out / "run_log.jsonl")
    assert any(
        e.get("event") == "auth_probe" and e.get("result") == "expired"
        for e in events
    )


def test_run_bundle_probe_error_exit_two(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    bundle = _make_bundle(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(
        orchestrator.auth_profiles,
        "get_profile",
        lambda name: _fake_profile(tmp_path),
    )
    monkeypatch.setattr(orchestrator, "probe_verify_url", lambda url, sp: "error")
    rc = orchestrator.run_bundle(bundle, out)
    assert rc == orchestrator.EXIT_SYS_ERROR


def test_run_bundle_valid_script_success_exit_zero(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    bundle = _make_bundle(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(
        orchestrator.auth_profiles,
        "get_profile",
        lambda name: _fake_profile(tmp_path),
    )
    monkeypatch.setattr(orchestrator, "probe_verify_url", lambda url, sp: "valid")
    monkeypatch.setattr(orchestrator, "_run_script_wrapper", lambda **kw: 0)
    rc = orchestrator.run_bundle(bundle, out)
    assert rc == orchestrator.EXIT_OK
    events = _read_jsonl(out / "run_log.jsonl")
    assert any(
        e.get("event") == "auth_probe" and e.get("result") == "valid" for e in events
    )
    assert any(e.get("event") == "scenario_done" for e in events)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["exit_code"] == 0
    assert meta["alias"] == "packaged"


def test_run_bundle_valid_script_fail_exit_one(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUTH_PROFILES_DIR", str(tmp_path / "auth-profiles"))
    bundle = _make_bundle(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(
        orchestrator.auth_profiles,
        "get_profile",
        lambda name: _fake_profile(tmp_path),
    )
    monkeypatch.setattr(orchestrator, "probe_verify_url", lambda url, sp: "valid")
    monkeypatch.setattr(orchestrator, "_run_script_wrapper", lambda **kw: 5)
    rc = orchestrator.run_bundle(bundle, out)
    assert rc == orchestrator.EXIT_SCENARIO_FAIL
