"""bundle.zip 패킹 + sanitize — 녹화 PC 측 산출물.

녹화 PC 의 sess_dir 에서 모니터링 PC 가 받을 portable bundle 을 만든다.

보안 원칙:
- ``*.storage.json`` / 호스트 절대경로 / 자격증명 평문 절대 포함 X
- ``script.py`` 는 sanitize (fill password 패턴 → placeholder) 후 잔존 자격증명
  의심 라인이 있으면 호출자에게 422 류 예외로 재검토 요구.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
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


# --- README 동적 생성 ---------------------------------------------------------


_README_TEMPLATE = """\
이 시나리오는 다음 alias 의 storage 가 모니터링 PC 에 시드되어 있어야 합니다:
  alias: {alias}

시드 방법 (모니터링 PC 에서):
  - Replay UI: Login Profile 카드 → [+ alias 추가] → [🌱 시드]
  - CLI:       python -m monitor profile seed {alias} --target <site URL>

실행 (권장 — 스크린샷·스텝 결과 자동 캡처):
  - Replay UI: Bundle 카드 [⬆ 업로드] → [▶ 실행]
  - CLI:       python -m monitor replay <이 zip> --out <결과 디렉토리>

직접 실행 (디버깅용, 스크린샷 없음):
  unzip <이 zip> -d ./run-dir
  cd run-dir
  # storage_state 경로를 new_context() 에 직접 주입하도록 script.py 헤더 수정
  python script.py

만료 감지 URL: {verify_url}

스크립트 출처:
  source_file: {source_file}
  source_kind: {source_kind}
  generated_at: {generated_at}
"""


def render_readme(alias: str, verify_url: str, provenance: dict) -> str:
    return _README_TEMPLATE.format(
        alias=alias,
        verify_url=verify_url,
        source_file=provenance.get("source_file", "?"),
        source_kind=provenance.get("source_kind", "?"),
        generated_at=provenance.get("generated_at", "?"),
    )


# --- pack / unpack -----------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pack_bundle(
    sess_dir: Path,
    alias: str,
    verify_url: str,
    *,
    script_source: Optional[str] = None,
    consent_plain_pw: bool = False,
) -> bytes:
    """녹화 sess_dir → portable bundle.zip bytes.

    Args:
        sess_dir: 녹화 세션 디렉토리 (``storage.session_dir(sid)`` 결과).
        alias: 모니터링 PC 카탈로그의 alias 이름.
        verify_url: 만료 감지용 URL.
        script_source: sess_dir 안 .py 파일명 (None 이면 자동 선택).
        consent_plain_pw: Login Profile 미적용 녹화에서 sanitize 후에도
            자격증명 잔존 시 사용자가 명시 동의했는지 (UI 모달의 [✓ 동의]).

    Raises:
        MetadataMissingError · ScriptSourceAmbiguousError ·
        ScriptSourceMissingError · PlainCredentialDetectedError.
    """
    if not sess_dir.is_dir():
        raise MetadataMissingError(f"sess_dir 가 디렉토리가 아닙니다: {sess_dir}")
    meta_path = sess_dir / "metadata.json"
    if not meta_path.is_file():
        raise MetadataMissingError(f"metadata.json 가 없습니다: {meta_path}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    src_py_path = select_script_source(sess_dir, script_source)
    src_text = src_py_path.read_text(encoding="utf-8")

    sanitized, diffs = sanitize_script(src_text)

    residue = grep_credential_residue(sanitized)
    if residue:
        # Login Profile 적용 여부 = metadata.auth_profile 키 존재 여부 (D8 / A2).
        login_profile_applied = bool(metadata.get("auth_profile"))
        if login_profile_applied:
            raise PlainCredentialDetectedError(
                "Login Profile 적용 녹화이지만 자격증명 의심 라인이 잔존합니다 "
                "(위양성 가능 — 사용자 검토 후 재시도)",
                diff_lines=[f"  L{n}: {t}" for n, t in residue],
            )
        if not consent_plain_pw:
            raise PlainCredentialDetectedError(
                "Login Profile 미적용 녹화 — sanitize 후에도 자격증명 의심 라인이 잔존합니다. "
                "diff 검토 후 consent_plain_pw=true 명시 동의 필요",
                diff_lines=diffs + [f"잔존 L{n}: {t}" for n, t in residue],
            )
        # 미적용 + 동의 → 통과 (사용자 책임).

    provenance = {
        "source_file": src_py_path.name,
        "source_kind": _classify_source_kind(src_py_path.name),
        "generated_at": _utc_now_iso(),
    }
    bundle_meta = dict(metadata)
    bundle_meta["auth_bundle"] = {"alias": alias, "verify_url": verify_url}
    bundle_meta["script_provenance"] = provenance

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("script.py", sanitized)
        z.writestr(
            "metadata.json",
            json.dumps(bundle_meta, indent=2, ensure_ascii=False),
        )
        scenario_p = sess_dir / "scenario.json"
        if scenario_p.is_file():
            z.write(scenario_p, "scenario.json")
        scenario_healed_p = sess_dir / "scenario.healed.json"
        if scenario_healed_p.is_file():
            z.write(scenario_healed_p, "scenario.healed.json")
        z.writestr("README.txt", render_readme(alias, verify_url, provenance))
    return buf.getvalue()


def unpack_bundle(zip_bytes: bytes, target_dir: Path) -> dict:
    """bundle.zip 을 ``target_dir`` 에 풀고 핵심 정보를 dict 로 반환.

    Returns: ``{"alias", "verify_url", "script_path", "script_provenance"}``.

    보안: 절대경로 / 상위 디렉토리 탈출 (zip slip) 거부.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise BundleError(f"유효하지 않은 zip: {e}") from e
    with zf as z:
        for name in z.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise BundleError(f"안전하지 않은 zip entry 거부: {name}")
        z.extractall(target_dir)

    meta_path = target_dir / "metadata.json"
    if not meta_path.is_file():
        raise BundleError("bundle 에 metadata.json 가 없습니다")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    auth_bundle = meta.get("auth_bundle") or {}
    script_path = target_dir / "script.py"
    if not script_path.is_file():
        raise BundleError("bundle 에 script.py 가 없습니다")
    return {
        "alias": auth_bundle.get("alias"),
        "verify_url": auth_bundle.get("verify_url"),
        "script_path": script_path,
        "script_provenance": meta.get("script_provenance") or {},
    }
