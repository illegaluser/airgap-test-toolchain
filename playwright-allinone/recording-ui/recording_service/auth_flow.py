"""bundle.zip 패킹 + sanitize — 녹화 PC 측 산출물.

녹화 PC 의 sess_dir 에서 모니터링 PC 가 받을 portable bundle 을 만든다.

보안 원칙:
- ``*.storage.json`` / 호스트 절대경로 / 자격증명 평문 절대 포함 X
- ``script.py`` 는 sanitize (fill password 패턴 → placeholder) 후 잔존 자격증명
  의심 라인이 있으면 호출자에게 422 류 예외로 재검토 요구.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# --- 예외 ---------------------------------------------------------------------


class BundleError(Exception):
    """패킹 / 언패킹 중 발생한 일반 오류 (HTTP 422 매핑)."""


class ScriptSourceAmbiguousError(BundleError):
    """sess_dir 에 .py 가 여러 개고 호출자가 명시하지 않음."""


class ScriptSourceMissingError(BundleError):
    """명시된 script_source 가 sess_dir 에 존재하지 않음."""


class PlainCredentialDetectedError(BundleError):
    """sanitize 후에도 자격증명 의심 라인이 잔존하고 사용자 동의가 없음."""

    def __init__(self, message: str, diff_lines: list[str]):
        super().__init__(message)
        self.diff_lines = diff_lines


class MetadataMissingError(BundleError):
    """sess_dir / metadata.json 자체가 존재하지 않음."""


# --- 자격증명 sanitize / detect ----------------------------------------------


_PLACEHOLDER = "__REPLACED_BY_BUNDLE_SANITIZER__"


# Playwright codegen 의 일반적인 fill 패턴.
# 예: page.get_by_label("password").fill("secret123")
#     page.locator("input[name=pw]").fill('xxx')
# selector 부분에 password / pw / passwd / secret 단어가 들어가는 케이스를
# 노린다. 너무 넓게 잡으면 정상 selector 까지 망가뜨리므로 fill() 짝과 함께만 매칭.
_FILL_PASSWORD_PATTERN = re.compile(
    r'((?:locator|fill|get_by_label|get_by_placeholder|get_by_role)\s*\([^)]*?'
    r'(?:password|pw|passwd|secret)[^)]*?\)\s*\.fill\s*\()'
    r'(["\'])([^"\']*)(["\'])',
    re.IGNORECASE,
)

# 변수 / 키-값 평문 자격증명 감지. sanitize 후 잔존 검출에 사용.
_PLAINTEXT_HINTS = re.compile(
    r"\b(password|pw|passwd|secret)\s*[=:]\s*['\"][^'\"]+['\"]",
    re.IGNORECASE,
)


def sanitize_script(text: str) -> tuple[str, list[str]]:
    """``script.py`` 안의 fill(password) 류를 placeholder 로 치환.

    Returns:
        (sanitized_text, diff_lines).  diff_lines 는 ``- before`` / ``+ after`` 묶음.
    """
    diffs: list[str] = []

    def _repl(m: re.Match) -> str:
        before_full = m.group(0)
        prefix = m.group(1)
        quote = m.group(2)
        value = m.group(3)
        if not value or value == _PLACEHOLDER:
            return before_full
        after = f"{prefix}{quote}{_PLACEHOLDER}{quote}"
        diffs.append(f"- {before_full}")
        diffs.append(f"+ {after}")
        return after

    new_text = _FILL_PASSWORD_PATTERN.sub(_repl, text)
    return new_text, diffs


def grep_credential_residue(text: str) -> list[tuple[int, str]]:
    """sanitize 후에도 남은 자격증명 의심 라인을 [(1-based line, text)] 로."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # 우리가 치환한 placeholder 가 들어있는 라인은 그 부분만 빼고 검사.
        cleaned = line.replace(_PLACEHOLDER, "") if _PLACEHOLDER in line else line
        if _PLAINTEXT_HINTS.search(cleaned):
            out.append((i, line.rstrip()))
    return out


# --- script source 선택 ------------------------------------------------------


def _list_py_files(sess_dir: Path) -> list[str]:
    return sorted(
        p.name for p in sess_dir.iterdir()
        if p.is_file() and p.suffix == ".py"
    )


def select_script_source(sess_dir: Path, requested: Optional[str]) -> Path:
    """sess_dir 안에서 사용할 .py 를 결정.

    명시 없으면 .py 가 정확히 한 개여야 한다 (보통 ``original.py``).
    여러 개 (``original.py`` + ``regression_test.py``) 면 명시 필수.
    """
    available = _list_py_files(sess_dir)
    if not available:
        raise ScriptSourceMissingError("sess_dir 에 .py 파일이 하나도 없습니다")
    if requested is None:
        if len(available) > 1:
            raise ScriptSourceAmbiguousError(
                f".py 가 여러 개 있어 자동 선택 불가: {available}. script_source 를 명시하세요"
            )
        requested = available[0]
    if requested not in available:
        raise ScriptSourceMissingError(
            f"요청한 스크립트 '{requested}' 가 sess_dir 에 없습니다 (가용: {available})"
        )
    return sess_dir / requested


def _classify_source_kind(filename: str) -> str:
    if filename == "original.py":
        return "codegen"
    if filename == "regression_test.py":
        return "llm_healed"
    return "manual"


# D17 — pack_bundle / unpack_bundle / render_readme 제거. 단일 .py 일원화 후
# Recording UI 의 ⬇ 다운로드 가 sanitize_script 만 통과시켜 .py 응답하고, Replay
# UI 가 .py 를 받아 사용자가 alias / verify_url 을 명시. zip packaging 불필요.
