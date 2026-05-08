"""orchestrator — bundle.zip 한 개를 실행하는 핵심 함수.

Replay UI 의 Run 카드, CLI 의 ``python -m monitor replay`` 가 공통으로 호출.

흐름:
    1. bundle.zip 풀어 임시 작업 디렉토리에 배치
    2. metadata 의 ``auth_bundle.alias`` 로 카탈로그 조회
       → storage_state 경로 + fingerprint
    3. ``verify_url`` probe (5s 타임아웃) — 만료 감지
    4. valid 면 ``codegen_trace_wrapper`` subprocess 로 ``script.py`` 실행
    5. 생성된 ``trace.zip`` 을 ``trace_parser`` 로 파싱 → run_log + 스크린샷
    6. ``out_dir`` 에 결과 누적 + exit code 분기 (계획 §8.3)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 모듈 위치는 monitor-runtime zip 에 동봉된 src/ 와 동일하므로 절대 import 가능.
from recording_service import auth_flow, trace_parser
from zero_touch_qa import auth_profiles


# Exit codes (계획 §8.3).
EXIT_OK = 0
EXIT_SCENARIO_FAIL = 1
EXIT_SYS_ERROR = 2
EXIT_SEED_EXPIRED = 3

# Probe 타임아웃 (계획 C4 — 1차 보수적 5s).
PROBE_TIMEOUT_S = 5.0

# 메인 시나리오 실행 타임아웃 (10 분 — codegen wrapper subprocess 가 끝나기 전 강제 종료).
SCENARIO_TIMEOUT_S = 600

# verify_url 결과가 이 패턴이면 expired 로 판정.
_LOGIN_PATH_PATTERN = re.compile(
    r"/(login|signin|signon|auth(?:enticat\w*)?|sso)(?:[/?#]|$)",
    re.IGNORECASE,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(jsonl_fp, **fields) -> None:
    """run_log.jsonl 에 한 줄 append + flush."""
    rec = {"ts": _utc_iso(), **fields}
    jsonl_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    jsonl_fp.flush()


# --- probe ------------------------------------------------------------------


def probe_verify_url(verify_url: str, storage_path: Optional[str]) -> str:
    """verify_url 이 만료되었는지 확인.

    Returns: ``"valid"`` / ``"expired"`` / ``"error"``.

    "error" 는 네트워크 / Playwright 오류 등 사용자 의도로 결정 못 하는 케이스.
    호출자가 EXIT_SYS_ERROR 분기로 처리.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return "error"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx_kwargs: dict = {}
                if storage_path and Path(storage_path).is_file():
                    ctx_kwargs["storage_state"] = storage_path
                context = browser.new_context(**ctx_kwargs)
                page = context.new_page()
                timeout_ms = int(PROBE_TIMEOUT_S * 1000)
                page.set_default_timeout(timeout_ms)
                try:
                    page.goto(verify_url, timeout=timeout_ms, wait_until="domcontentloaded")
                except Exception:
                    return "expired"
                final_url = page.url or ""
                if _LOGIN_PATH_PATTERN.search(final_url):
                    return "expired"
                # 보조 신호 — password input 노출.
                try:
                    if page.locator("input[type=password]").count() > 0:
                        return "expired"
                except Exception:
                    pass
                return "valid"
            finally:
                browser.close()
    except Exception:
        return "error"


# --- script subprocess ------------------------------------------------------


def _run_script_wrapper(
    unpack_dir: Path,
    script_name: str,
    storage_path: Optional[str],
    fingerprint_env: dict,
) -> int:
    """``codegen_trace_wrapper`` 를 subprocess 로 호출 (계획의 wrapper 재사용)."""
    env = os.environ.copy()
    env["CODEGEN_SESSION_DIR"] = str(unpack_dir)
    env["CODEGEN_SCRIPT"] = script_name
    env["CODEGEN_HEADLESS"] = "1"  # 모니터링은 headless 강제.
    if storage_path:
        env["AUTH_STORAGE_STATE_IN"] = storage_path
    env.update(fingerprint_env or {})
    cmd = [sys.executable, "-m", "recording_service.codegen_trace_wrapper"]
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=SCENARIO_TIMEOUT_S,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        return -1


# --- 결과 메타 저장 -----------------------------------------------------------


def _save_meta(
    meta_file: Path,
    *,
    started_at: str,
    finished_at: str,
    bundle: Path,
    alias: Optional[str],
    provenance: dict,
    exit_code: int,
) -> None:
    meta = {
        "bundle": str(bundle),
        "alias": alias,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "script_provenance": provenance,
    }
    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_exit(out_dir: Path, code: int) -> None:
    (out_dir / "exit_code").write_text(str(code))


# --- 메인 진입점 -------------------------------------------------------------


def run_bundle(bundle_zip_path: Path, out_dir: Path) -> int:
    """bundle.zip 한 개를 실행해 결과를 ``out_dir`` 에 저장.

    Returns: exit code (§8.3).
    """
    bundle_zip_path = Path(bundle_zip_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    run_log_path = out_dir / "run_log.jsonl"
    meta_file = out_dir / "meta.json"

    started_at = _utc_iso()

    with run_log_path.open("w", encoding="utf-8") as f_log:
        with tempfile.TemporaryDirectory(prefix="replay-bundle-") as td:
            unpack_dir = Path(td) / "unpacked"

            # 1. unpack.
            try:
                info = auth_flow.unpack_bundle(
                    bundle_zip_path.read_bytes(), unpack_dir
                )
            except auth_flow.BundleError as e:
                _emit(f_log, event="system_error", reason=f"unpack 실패: {e}")
                _write_exit(out_dir, EXIT_SYS_ERROR)
                _save_meta(
                    meta_file,
                    started_at=started_at,
                    finished_at=_utc_iso(),
                    bundle=bundle_zip_path,
                    alias=None,
                    provenance={},
                    exit_code=EXIT_SYS_ERROR,
                )
                return EXIT_SYS_ERROR

            alias = info["alias"]
            verify_url = info["verify_url"]
            script_path = info["script_path"]
            provenance = info["script_provenance"] or {}

            if not alias or not verify_url:
                _emit(
                    f_log,
                    event="system_error",
                    reason="bundle metadata 에 alias / verify_url 누락",
                )
                _write_exit(out_dir, EXIT_SYS_ERROR)
                _save_meta(
                    meta_file,
                    started_at=started_at,
                    finished_at=_utc_iso(),
                    bundle=bundle_zip_path,
                    alias=alias,
                    provenance=provenance,
                    exit_code=EXIT_SYS_ERROR,
                )
                return EXIT_SYS_ERROR

            # 2. 카탈로그 조회.
            try:
                profile = auth_profiles.get_profile(alias)
            except auth_profiles.AuthProfileError as e:
                _emit(
                    f_log,
                    event="auth_seed_expired",
                    alias=alias,
                    reason=f"profile_not_found: {e}",
                )
                _write_exit(out_dir, EXIT_SEED_EXPIRED)
                _save_meta(
                    meta_file,
                    started_at=started_at,
                    finished_at=_utc_iso(),
                    bundle=bundle_zip_path,
                    alias=alias,
                    provenance=provenance,
                    exit_code=EXIT_SEED_EXPIRED,
                )
                return EXIT_SEED_EXPIRED

            storage_path: Optional[str] = (
                str(profile.storage_path)
                if profile.storage_path.is_file()
                else None
            )
            fingerprint_env = (
                profile.fingerprint.to_env() if profile.fingerprint else {}
            )

            # 3. probe.
            probe_result = probe_verify_url(verify_url, storage_path)
            _emit(f_log, event="auth_probe", result=probe_result)
            if probe_result == "expired":
                _emit(f_log, event="auth_seed_expired", alias=alias)
                _write_exit(out_dir, EXIT_SEED_EXPIRED)
                _save_meta(
                    meta_file,
                    started_at=started_at,
                    finished_at=_utc_iso(),
                    bundle=bundle_zip_path,
                    alias=alias,
                    provenance=provenance,
                    exit_code=EXIT_SEED_EXPIRED,
                )
                return EXIT_SEED_EXPIRED
            if probe_result == "error":
                _emit(
                    f_log,
                    event="system_error",
                    reason="probe 실패 (Playwright / 네트워크)",
                )
                _write_exit(out_dir, EXIT_SYS_ERROR)
                _save_meta(
                    meta_file,
                    started_at=started_at,
                    finished_at=_utc_iso(),
                    bundle=bundle_zip_path,
                    alias=alias,
                    provenance=provenance,
                    exit_code=EXIT_SYS_ERROR,
                )
                return EXIT_SYS_ERROR

            # 4. 스크립트 실행 (codegen_trace_wrapper subprocess).
            script_exit = _run_script_wrapper(
                unpack_dir=unpack_dir,
                script_name=script_path.name,
                storage_path=storage_path,
                fingerprint_env=fingerprint_env,
            )

            # 5. trace 추출.
            trace_zip = unpack_dir / "trace.zip"
            steps_written = 0
            if trace_zip.is_file():
                steps_written = trace_parser.parse_trace(
                    trace_zip,
                    out_run_log=out_dir / "codegen_run_log.jsonl",
                    out_screenshots_dir=screenshots_dir,
                    prefer_png=True,
                )
                shutil.copy2(trace_zip, out_dir / "trace.zip")

            # 6. 최종 exit code.
            if script_exit == 0:
                final_exit = EXIT_OK
            elif script_exit == -1:
                _emit(f_log, event="scenario_timeout", timeout_s=SCENARIO_TIMEOUT_S)
                final_exit = EXIT_SCENARIO_FAIL
            else:
                final_exit = EXIT_SCENARIO_FAIL
            _emit(
                f_log,
                event="scenario_done",
                script_exit=script_exit,
                steps=steps_written,
                final_exit=final_exit,
            )
            _write_exit(out_dir, final_exit)
            _save_meta(
                meta_file,
                started_at=started_at,
                finished_at=_utc_iso(),
                bundle=bundle_zip_path,
                alias=alias,
                provenance=provenance,
                exit_code=final_exit,
            )
            return final_exit
