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
from recording_service import trace_parser
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
    headed: bool = True,
    slow_mo_ms: Optional[int] = None,
) -> int:
    """``codegen_trace_wrapper`` 를 subprocess 로 호출 (계획의 wrapper 재사용)."""
    env = os.environ.copy()
    env["CODEGEN_SESSION_DIR"] = str(unpack_dir)
    env["CODEGEN_SCRIPT"] = script_name
    # D9 — 운영 기본은 headed. headless 는 사용자 명시 옵트인.
    # codegen_trace_wrapper 의 _install_launch_overrides 는 CODEGEN_HEADED=1
    # 또는 CODEGEN_HEADLESS=1 *둘 중 하나가 명시* 되어야 monkey-patch 를 설치.
    # 이전 `"0" if headed else "1"` 패턴은 headed 케이스에서 patch 가 미설치되어
    # 사용자 .py 의 launch() 가 Playwright default(headless=True) 로 떨어지던
    # 회귀 (2026-05-11). Recording UI 의 replay_proxy.py 와 같은 형태로 정정.
    if headed:
        env["CODEGEN_HEADED"] = "1"
    else:
        env["CODEGEN_HEADLESS"] = "1"
    # 액션 사이 지연 — wrapper 의 monkey-patch 가 launch() kwargs 에 slow_mo 주입.
    if slow_mo_ms and slow_mo_ms > 0:
        env["CODEGEN_SLOW_MO_MS"] = str(int(slow_mo_ms))
    # Windows 콘솔 cp949 한글 깨짐 회귀 방지 — 자식 stdout/stderr UTF-8 강제.
    env.setdefault("PYTHONIOENCODING", "utf-8")
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
            encoding="utf-8",
            errors="replace",
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


# --- 신규 진입점 (D17) — 단일 .py 흐름 -----------------------------------


def run_script(
    script_path: Path,
    out_dir: Path,
    alias: Optional[str] = None,
    verify_url: Optional[str] = None,
    headed: bool = True,
    slow_mo_ms: Optional[int] = None,
) -> int:
    """단일 ``.py`` 시나리오를 실행해 결과를 ``out_dir`` 에 저장 (D17 일원화).

    번들 zip / metadata 없이 사용자가 직접 정한 (alias, verify_url) 만 받아
    실행한다. ``alias`` 가 ``None`` 또는 빈 문자열이면 *비로그인* 시나리오로
    간주 — storage_state 미주입 + verify probe 스킵.

    Args:
        script_path: 실행할 Playwright ``.py``. 실재 파일이어야 함.
        out_dir: 결과 디렉토리 (run_log.jsonl, screenshots, trace.zip, meta.json).
        alias: 적용할 로그인 프로파일 이름. None/"" 이면 비로그인.
        verify_url: 만료 감지 probe URL. ``alias`` 가 있을 때만 의미. None 이면
            카탈로그 entry 의 ``verify.service_url`` 로 fallback.
        headed: D9 — 운영 기본 True.
        slow_mo_ms: 양수면 wrapper monkey-patch 가 launch() 의 ``slow_mo`` 인자에
            ms 단위 주입. 사람이 눈으로 따라가며 디버깅할 때 유용.

    Returns: exit code (§8.3 — EXIT_OK / EXIT_SCENARIO_FAIL / EXIT_SYS_ERROR /
        EXIT_SEED_EXPIRED).
    """
    script_path = Path(script_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    run_log_path = out_dir / "run_log.jsonl"
    meta_file = out_dir / "meta.json"

    started_at = _utc_iso()

    if not script_path.is_file():
        with run_log_path.open("w", encoding="utf-8") as f_log:
            _emit(f_log, event="system_error", reason=f"script 파일 없음: {script_path}")
        _write_exit(out_dir, EXIT_SYS_ERROR)
        _save_meta(
            meta_file, started_at=started_at, finished_at=_utc_iso(),
            bundle=script_path, alias=alias, provenance={}, exit_code=EXIT_SYS_ERROR,
        )
        return EXIT_SYS_ERROR

    # 비로그인 정규화 — 빈 문자열도 None 처리.
    alias_norm = (alias or "").strip() or None

    with run_log_path.open("w", encoding="utf-8") as f_log:
        with tempfile.TemporaryDirectory(prefix="replay-script-") as td:
            unpack_dir = Path(td) / "unpacked"
            unpack_dir.mkdir(parents=True)
            # codegen_trace_wrapper 가 ARTIFACTS_DIR 로 사용 — script.py 만 복사.
            target_script = unpack_dir / "script.py"
            target_script.write_bytes(script_path.read_bytes())

            storage_path: Optional[str] = None
            fingerprint_env: dict = {}

            if alias_norm:
                # 카탈로그 조회.
                try:
                    profile = auth_profiles.get_profile(alias_norm)
                except auth_profiles.AuthProfileError as e:
                    _emit(f_log, event="auth_seed_expired", alias=alias_norm,
                          reason=f"profile_not_found: {e}")
                    _write_exit(out_dir, EXIT_SEED_EXPIRED)
                    _save_meta(meta_file, started_at=started_at, finished_at=_utc_iso(),
                               bundle=script_path, alias=alias_norm, provenance={},
                               exit_code=EXIT_SEED_EXPIRED)
                    return EXIT_SEED_EXPIRED

                storage_path = (
                    str(profile.storage_path) if profile.storage_path.is_file() else None
                )
                fingerprint_env = profile.fingerprint.to_env() if profile.fingerprint else {}

                # verify_url 결정 — 사용자 입력 우선, 없으면 프로파일 fallback.
                effective_verify = (verify_url or "").strip() or profile.verify.service_url
                if effective_verify:
                    probe_result = probe_verify_url(effective_verify, storage_path)
                    _emit(f_log, event="auth_probe", result=probe_result, url=effective_verify)
                    if probe_result == "expired":
                        _emit(f_log, event="auth_seed_expired", alias=alias_norm)
                        _write_exit(out_dir, EXIT_SEED_EXPIRED)
                        _save_meta(meta_file, started_at=started_at, finished_at=_utc_iso(),
                                   bundle=script_path, alias=alias_norm, provenance={},
                                   exit_code=EXIT_SEED_EXPIRED)
                        return EXIT_SEED_EXPIRED
                    if probe_result == "error":
                        _emit(f_log, event="system_error",
                              reason="probe 실패 (Playwright / 네트워크)")
                        _write_exit(out_dir, EXIT_SYS_ERROR)
                        _save_meta(meta_file, started_at=started_at, finished_at=_utc_iso(),
                                   bundle=script_path, alias=alias_norm, provenance={},
                                   exit_code=EXIT_SYS_ERROR)
                        return EXIT_SYS_ERROR
            else:
                # 비로그인 — probe / storage_state 모두 스킵.
                _emit(f_log, event="auth_skip", reason="alias 미지정 — 비로그인 시나리오")

            # 스크립트 실행 (codegen_trace_wrapper subprocess).
            script_exit = _run_script_wrapper(
                unpack_dir=unpack_dir,
                script_name=target_script.name,
                storage_path=storage_path,
                fingerprint_env=fingerprint_env,
                headed=headed,
                slow_mo_ms=slow_mo_ms,
            )

            # trace 파싱.
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

            # 최종 exit code.
            if script_exit == 0:
                final_exit = EXIT_OK
            elif script_exit == -1:
                _emit(f_log, event="scenario_timeout", timeout_s=SCENARIO_TIMEOUT_S)
                final_exit = EXIT_SCENARIO_FAIL
            else:
                final_exit = EXIT_SCENARIO_FAIL
            _emit(f_log, event="scenario_done",
                  script_exit=script_exit, steps=steps_written, final_exit=final_exit)
            _write_exit(out_dir, final_exit)
            _save_meta(meta_file, started_at=started_at, finished_at=_utc_iso(),
                       bundle=script_path, alias=alias_norm, provenance={},
                       exit_code=final_exit)
            return final_exit


