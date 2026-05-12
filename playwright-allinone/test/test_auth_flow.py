"""auth_flow 회귀 — D17 (2026-05-11) .py 일원화 이후의 *현재 살아있는* 보안 게이트.

- ``sanitize_script(text)`` — fill(password) 패턴을 placeholder 로 치환
- ``grep_credential_residue(text)`` — sanitize 후 잔존 자격증명 의심 라인 탐지
- ``select_script_source(sess_dir, requested)`` — .py 선택 (모호/부재 분기)

D17 이전의 ``pack_bundle / unpack_bundle / render_readme`` 는 제거됨 — bundle.zip
흐름 폐기, .py 일원화. 본 테스트는 *현재 면* 의 정상/엣지 분기만 커버한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recording_service.auth_flow import (
    ScriptSourceAmbiguousError,
    ScriptSourceMissingError,
    grep_credential_residue,
    sanitize_script,
    select_script_source,
)


PLACEHOLDER = "__REPLACED_BY_BUNDLE_SANITIZER__"


# ─────────────────────────────────────────────────────────────────────────
# sanitize_script
# ─────────────────────────────────────────────────────────────────────────


def test_sanitize_replaces_password_fill():
    src = 'page.get_by_label("password").fill("secret123")'
    out, diffs = sanitize_script(src)
    assert "secret123" not in out
    assert PLACEHOLDER in out
    assert len(diffs) == 2  # - before / + after pair
    assert diffs[0].startswith("- ")
    assert diffs[1].startswith("+ ")


def test_sanitize_replaces_pw_passwd_secret_variants():
    """selector 안에 pw / passwd / secret 단어가 있으면 매치."""
    src = (
        'page.locator("input[name=pw]").fill("p1")\n'
        'page.get_by_placeholder("passwd").fill("p2")\n'
        'page.get_by_role("textbox", name="secret").fill("p3")\n'
    )
    out, diffs = sanitize_script(src)
    for v in ("p1", "p2", "p3"):
        assert f'"{v}"' not in out, f"{v!r} 잔존"
    assert out.count(PLACEHOLDER) == 3
    assert len(diffs) == 6  # 3 fills × (before+after)


def test_sanitize_leaves_unrelated_fills_intact():
    """username/email fill 은 보호 대상 아님 — 치환되면 안 됨."""
    src = 'page.get_by_label("username").fill("alice")'
    out, _ = sanitize_script(src)
    assert out == src
    assert "alice" in out


def test_sanitize_idempotent_on_already_placeholder():
    """이미 placeholder 인 값은 다시 변경 없음 (diff 0)."""
    src = f'page.get_by_label("password").fill("{PLACEHOLDER}")'
    out, diffs = sanitize_script(src)
    assert out == src
    assert diffs == []


def test_sanitize_handles_empty_input():
    out, diffs = sanitize_script("")
    assert out == ""
    assert diffs == []


# ─────────────────────────────────────────────────────────────────────────
# grep_credential_residue
# ─────────────────────────────────────────────────────────────────────────


def test_residue_detects_plaintext_assignment():
    """variable = "value" 패턴은 sanitize 가 못 잡으므로 residue 가 잡아야."""
    text = 'password = "leaked-secret"\nuser = "alice"\n'
    out = grep_credential_residue(text)
    assert len(out) == 1
    line_no, line_text = out[0]
    assert line_no == 1
    assert "password" in line_text


def test_residue_ignores_placeholder_substituted_lines():
    """sanitize 가 채워둔 placeholder 가 있는 라인은 *그 부분 빼고* 검사 — 오탐 방지."""
    text = f'page.get_by_label("password").fill("{PLACEHOLDER}")\n'
    out = grep_credential_residue(text)
    assert out == []


def test_residue_returns_1_based_line_numbers():
    text = "line1\nline2\npassword='x'\n"
    out = grep_credential_residue(text)
    assert out == [(3, "password='x'")]


# ─────────────────────────────────────────────────────────────────────────
# select_script_source
# ─────────────────────────────────────────────────────────────────────────


def test_select_single_py_returns_that_path(tmp_path: Path):
    (tmp_path / "original.py").write_text("# pw")
    result = select_script_source(tmp_path, requested=None)
    assert result == tmp_path / "original.py"


def test_select_multiple_py_without_request_raises(tmp_path: Path):
    (tmp_path / "original.py").write_text("a")
    (tmp_path / "regression_test.py").write_text("b")
    with pytest.raises(ScriptSourceAmbiguousError):
        select_script_source(tmp_path, requested=None)


def test_select_with_explicit_request_returns_match(tmp_path: Path):
    (tmp_path / "original.py").write_text("a")
    (tmp_path / "regression_test.py").write_text("b")
    result = select_script_source(tmp_path, requested="regression_test.py")
    assert result == tmp_path / "regression_test.py"


def test_select_explicit_request_missing_raises(tmp_path: Path):
    (tmp_path / "original.py").write_text("a")
    with pytest.raises(ScriptSourceMissingError):
        select_script_source(tmp_path, requested="not_there.py")


def test_select_no_py_files_raises(tmp_path: Path):
    with pytest.raises(ScriptSourceMissingError):
        select_script_source(tmp_path, requested=None)


# ─────────────────────────────────────────────────────────────────────────
# 추가 경계 케이스 — fill 패턴 변형 + residue 더 많은 패턴
# ─────────────────────────────────────────────────────────────────────────


def test_residue_detects_secret_assignment():
    """variable 이름이 ``secret`` 인 경우도 plaintext 패턴으로 잡아야."""
    text = 'secret = "leaked-value"\n'
    out = grep_credential_residue(text)
    assert len(out) == 1
    assert "secret" in out[0][1].lower()


def test_residue_case_insensitive():
    """``Password:`` 같은 대소문자 변형도 매치 — IGNORECASE 동작 확인."""
    text = 'Password: "leaked"\n'
    out = grep_credential_residue(text)
    assert len(out) == 1


def test_residue_returns_empty_for_clean_text():
    """credential 의심 키워드 없으면 빈 list."""
    out = grep_credential_residue('page.goto("https://x")\nuser = "alice"\n')
    assert out == []
