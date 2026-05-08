"""단위 테스트 — recording_service.auth_flow.

검증 범위 (계획 §18):
- pack/unpack round-trip
- script.py 포함 + sanitize 동작
- *.storage.json 미포함
- script.py 안 password/secret 0 매칭 (sanitize 후)
- README 동적 생성 (alias / verify_url 포함)
- script_provenance metadata 정확성 (codegen / llm_healed 두 케이스)
- sess_dir 에 .py 여러 개일 때 명시 누락 시 ScriptSourceAmbiguousError
- Login Profile 미적용 + sanitize 후 잔존 + consent 없음 → PlainCredentialDetectedError
- Login Profile 적용 + 잔존 → PlainCredentialDetectedError (위양성 의심)
- Login Profile 미적용 + consent=True → 통과
- zip-slip (.. 또는 절대경로) 거부
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

# playwright-allinone/ 을 sys.path 에 추가 (test/ 의 부모).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from recording_service import auth_flow  # noqa: E402


# --- helpers -----------------------------------------------------------------


def _make_session(
    tmp_path: Path,
    *,
    with_regression: bool = False,
    with_password_fill: bool = False,
    extra_body: str = "",
    login_profile_applied: bool = True,
) -> Path:
    sess = tmp_path / "sess"
    sess.mkdir()
    metadata: dict = {"id": "sess", "target_url": "https://ex.com/login"}
    if login_profile_applied:
        metadata["auth_profile"] = "demo-profile"
    (sess / "metadata.json").write_text(json.dumps(metadata))

    body = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    browser = p.chromium.launch()\n"
        "    context = browser.new_context()\n"
        "    page = context.new_page()\n"
        '    page.goto("https://ex.com/login")\n'
    )
    if with_password_fill:
        body += '    page.get_by_label("password").fill("hunter2")\n'
        body += '    page.get_by_label("username").fill("alice")\n'
    body += '    page.click("text=Login")\n'
    if extra_body:
        body += extra_body
    (sess / "original.py").write_text(body)

    if with_regression:
        body2 = body + "# llm-healed marker\n"
        (sess / "regression_test.py").write_text(body2)

    (sess / "scenario.json").write_text(
        json.dumps([{"action": "goto", "url": "https://ex.com/login"}])
    )
    return sess


def _list_zip(zip_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        return sorted(z.namelist())


def _read_zip_text(zip_bytes: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        return z.read(name).decode("utf-8")


# --- 테스트 -------------------------------------------------------------------


def test_pack_unpack_round_trip(tmp_path: Path) -> None:
    sess = _make_session(tmp_path, with_password_fill=True)
    zb = auth_flow.pack_bundle(
        sess, alias="packaged", verify_url="https://ex.com/dashboard"
    )
    assert len(zb) > 0

    out = tmp_path / "out"
    info = auth_flow.unpack_bundle(zb, out)
    assert info["alias"] == "packaged"
    assert info["verify_url"] == "https://ex.com/dashboard"
    assert info["script_path"].is_file()
    assert info["script_provenance"]["source_kind"] == "codegen"


def test_zip_does_not_contain_storage_state(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    # storage.json 류를 sess_dir 에 둬도 zip 에 안 들어가는지 단언.
    (sess / "storage.json").write_text('{"cookies": []}')
    (sess / "auth.storage.json").write_text('{"cookies": []}')
    zb = auth_flow.pack_bundle(sess, alias="p", verify_url="https://x")
    names = _list_zip(zb)
    assert not any("storage" in n.lower() for n in names), names


def test_script_py_password_sanitized(tmp_path: Path) -> None:
    """fill(password=...) 패턴이 placeholder 로 치환됨."""
    sess = _make_session(tmp_path, with_password_fill=True)
    zb = auth_flow.pack_bundle(sess, alias="p", verify_url="https://x")
    script_text = _read_zip_text(zb, "script.py")
    assert "hunter2" not in script_text
    assert "__REPLACED_BY_BUNDLE_SANITIZER__" in script_text


def test_provenance_codegen_vs_llm_healed(tmp_path: Path) -> None:
    sess = _make_session(tmp_path, with_regression=True)

    zb1 = auth_flow.pack_bundle(
        sess, alias="p", verify_url="https://x", script_source="original.py"
    )
    meta1 = json.loads(_read_zip_text(zb1, "metadata.json"))
    assert meta1["script_provenance"]["source_file"] == "original.py"
    assert meta1["script_provenance"]["source_kind"] == "codegen"

    zb2 = auth_flow.pack_bundle(
        sess, alias="p", verify_url="https://x", script_source="regression_test.py"
    )
    meta2 = json.loads(_read_zip_text(zb2, "metadata.json"))
    assert meta2["script_provenance"]["source_file"] == "regression_test.py"
    assert meta2["script_provenance"]["source_kind"] == "llm_healed"


def test_ambiguous_script_source_raises(tmp_path: Path) -> None:
    """sess_dir 에 .py 가 여러 개고 호출자가 명시 안 하면 422."""
    sess = _make_session(tmp_path, with_regression=True)
    with pytest.raises(auth_flow.ScriptSourceAmbiguousError):
        auth_flow.pack_bundle(sess, alias="p", verify_url="https://x")


def test_missing_script_source_raises(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    with pytest.raises(auth_flow.ScriptSourceMissingError):
        auth_flow.pack_bundle(
            sess, alias="p", verify_url="https://x", script_source="no-such.py"
        )


def test_plain_credential_without_login_profile_raises(tmp_path: Path) -> None:
    """Login Profile 미적용 + sanitize 패턴에 안 잡히는 평문 → consent 없으면 거부."""
    sess = _make_session(
        tmp_path,
        login_profile_applied=False,
        extra_body='password = "topsecret123"\n',
    )
    with pytest.raises(auth_flow.PlainCredentialDetectedError) as ei:
        auth_flow.pack_bundle(sess, alias="p", verify_url="https://x")
    assert ei.value.diff_lines


def test_plain_credential_with_consent_passes(tmp_path: Path) -> None:
    """Login Profile 미적용 + consent=True → 통과 (사용자 책임)."""
    sess = _make_session(
        tmp_path,
        login_profile_applied=False,
        extra_body='password = "topsecret123"\n',
    )
    zb = auth_flow.pack_bundle(
        sess,
        alias="p",
        verify_url="https://x",
        consent_plain_pw=True,
    )
    script_text = _read_zip_text(zb, "script.py")
    assert "topsecret123" in script_text


def test_login_profile_applied_with_residue_raises(tmp_path: Path) -> None:
    """Login Profile 적용 녹화이지만 평문이 남으면 위양성 의심으로 422 (consent 무시)."""
    sess = _make_session(
        tmp_path,
        login_profile_applied=True,
        extra_body='password = "topsecret123"\n',
    )
    with pytest.raises(auth_flow.PlainCredentialDetectedError):
        auth_flow.pack_bundle(
            sess,
            alias="p",
            verify_url="https://x",
            consent_plain_pw=True,
        )


def test_readme_contains_alias_and_verify_url(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    zb = auth_flow.pack_bundle(
        sess, alias="my-alias", verify_url="https://yo.example.com/dash"
    )
    text = _read_zip_text(zb, "README.txt")
    assert "my-alias" in text
    assert "https://yo.example.com/dash" in text


def test_zip_slip_rejected(tmp_path: Path) -> None:
    """unpack_bundle 가 ../ 또는 절대경로 entry 를 거부."""
    target = tmp_path / "safe"
    bogus = io.BytesIO()
    with zipfile.ZipFile(bogus, "w") as z:
        z.writestr("../escape.py", "pass")
    with pytest.raises(auth_flow.BundleError):
        auth_flow.unpack_bundle(bogus.getvalue(), target)
