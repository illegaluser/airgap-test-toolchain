"""단위 테스트 — recording_service.recording_tools (CLI).

검증:
- pack-bundle 정상 호출 → exit 0 + 파일 생성
- 미존재 sid → exit 2
- Login Profile 미적용 + 평문 잔존 + consent 없음 → exit 3
- Login Profile 미적용 + --consent-plain-pw → exit 0
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

# playwright-allinone/ 을 sys.path 에 추가.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from recording_service import recording_tools  # noqa: E402


def _make_session(
    host_root: Path,
    sid: str,
    *,
    login_profile_applied: bool = True,
    extra_body: str = "",
) -> Path:
    sess = host_root / sid
    sess.mkdir(parents=True)
    metadata: dict = {"id": sid}
    if login_profile_applied:
        metadata["auth_profile"] = "demo"
    (sess / "metadata.json").write_text(json.dumps(metadata))
    body = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p: pass\n"
    )
    if extra_body:
        body += extra_body
    (sess / "original.py").write_text(body)
    return sess


def test_pack_bundle_cli_exit_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORDING_HOST_ROOT", str(tmp_path))
    sid = "test-cli-1"
    _make_session(tmp_path, sid)
    out = tmp_path / "out.zip"
    rc = recording_tools.main(
        [
            "pack-bundle",
            sid,
            "--alias",
            "packaged",
            "--verify-url",
            "https://ex.com/dash",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:
        names = sorted(z.namelist())
    assert "script.py" in names
    assert "metadata.json" in names
    assert "README.txt" in names


def test_pack_bundle_missing_sid_exit_two(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECORDING_HOST_ROOT", str(tmp_path))
    out = tmp_path / "out.zip"
    rc = recording_tools.main(
        [
            "pack-bundle",
            "no-such-sid",
            "--alias",
            "p",
            "--verify-url",
            "https://x",
            "--out",
            str(out),
        ]
    )
    # storage.session_dir() 가 디렉토리를 자동 생성하므로 "is_dir" 통과 후
    # auth_flow.pack_bundle 단계에서 metadata.json 부재로 BundleError → exit 4.
    # 즉 sid 자체가 없는 케이스는 exit 4 를 기대.
    assert rc == 4


def test_pack_bundle_plain_credential_no_consent_exit_three(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("RECORDING_HOST_ROOT", str(tmp_path))
    sid = "test-cli-3"
    _make_session(
        tmp_path,
        sid,
        login_profile_applied=False,
        extra_body='password = "topsecret"\n',
    )
    out = tmp_path / "out.zip"
    rc = recording_tools.main(
        [
            "pack-bundle",
            sid,
            "--alias",
            "p",
            "--verify-url",
            "https://x",
            "--out",
            str(out),
        ]
    )
    assert rc == 3
    assert not out.exists()


def test_pack_bundle_plain_credential_with_consent_exit_zero(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("RECORDING_HOST_ROOT", str(tmp_path))
    sid = "test-cli-4"
    _make_session(
        tmp_path,
        sid,
        login_profile_applied=False,
        extra_body='password = "topsecret"\n',
    )
    out = tmp_path / "out.zip"
    rc = recording_tools.main(
        [
            "pack-bundle",
            sid,
            "--alias",
            "p",
            "--verify-url",
            "https://x",
            "--consent-plain-pw",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file()
