"""TR.7 — Play (R-Plus). 두 가지 모드:

1. **Codegen Output Replay** — codegen 원본 ``original.py`` 를 호스트에서 그대로
   실행. 녹화한 동작이 화면에 그대로 재현 (headed).

2. **Play with LLM** — 변환된 14-DSL 시나리오 (``scenario.json``) 를 호스트
   zero_touch_qa executor 로 실행. healing/verify/mock 등 14-DSL 의 풀 기능
   동작 + 화면에 재생 (headed).

두 모드 모두 호스트 venv python 으로 subprocess 실행 — 컨테이너 docker exec
경로는 화면 표시 불가라 사용 안 함. ``<venv_py>`` 는 ``RECORDING_VENV_PY`` env
또는 ``sys.executable`` (= recording-service daemon 의 venv python).

P4 — auth-profile 통합:
    세션 ``metadata.json`` 의 ``auth_profile`` 필드 (D15) 가 있으면 재생 시작
    전에 verify 통과 강제 + storage_state 인자 + fingerprint env 자동 주입.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_REPLAY_TIMEOUT_SEC = int(
    os.environ.get("RECORDING_REPLAY_TIMEOUT_SEC", "300")
)


class ReplayProxyError(RuntimeError):
    """play 단계의 명시적 에러 (codegen / LLM 공통)."""


class ReplayAuthExpiredError(ReplayProxyError):
    """auth-profile verify 실패 — 재시드 필요 (P4.4).

    UI 가 만료 모달로 분기할 수 있도록 별도 예외 타입으로 분리.
    """

    def __init__(self, profile_name: str, detail: dict):
        super().__init__(
            f"auth-profile '{profile_name}' verify 실패 (재시드 필요): {detail}"
        )
        self.profile_name = profile_name
        self.detail = dict(detail)


def _load_session_metadata(host_session_dir: str) -> dict:
    """세션 디렉토리의 ``metadata.json`` 로드. 없으면 빈 dict."""
    p = Path(host_session_dir) / "metadata.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[replay] metadata.json 로드 실패 (%s): %s", p, e)
        return {}


def _resolve_auth_for_replay(
    host_session_dir: str,
    override: Optional[str] = None,
) -> tuple[Optional[str], Optional[dict], Optional[str]]:
    """metadata.json 의 ``auth_profile`` → (storage_path, fingerprint_env, profile_name).

    - 메타에 auth_profile 키가 없으면 ``(None, None, None)`` — 인증 없는 재생.
    - 프로파일 lookup / verify 실패 시 ``ReplayAuthExpiredError`` (P4.4).

    Args:
        override: R-Plus Play 호출 시 사용자가 명시한 auth_profile.
            - ``None`` (기본): metadata 값을 그대로 사용.
            - ``""`` (빈 문자열): 인증 없이 재생 — metadata 값 무시.
            - ``"<name>"``: metadata 값을 무시하고 그 프로파일로 재생.
    """
    if override is not None:
        if override == "":
            return None, None, None
        profile_name = override
    else:
        meta = _load_session_metadata(host_session_dir)
        profile_name = meta.get("auth_profile")
    if not profile_name:
        return None, None, None

    # auth_profiles 는 fcntl 의존성이라 lazy import.
    from zero_touch_qa import auth_profiles
    from zero_touch_qa.auth_profiles import (
        AuthProfileError, ProfileNotFoundError,
    )

    try:
        prof = auth_profiles.get_profile(profile_name)
    except ProfileNotFoundError as e:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "profile_not_found", "message": str(e)},
        ) from e
    except AuthProfileError as e:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "profile_error", "message": str(e)},
        ) from e

    try:
        ok, vdetail = auth_profiles.verify_profile(prof)
    except AuthProfileError as e:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "verify_error", "message": str(e)},
        ) from e
    if not ok:
        raise ReplayAuthExpiredError(
            profile_name, {"reason": "verify_failed", **vdetail},
        )

    return str(prof.storage_path), prof.fingerprint.to_env(), profile_name


@dataclass
class PlayResult:
    """호스트 subprocess 실행 결과 — codegen output / LLM 공통.

    codegen 원본은 평범한 Playwright 스크립트이므로 'PASS step 수' 같은 개념이
    없다 — returncode 0 = 끝까지 정상 실행. 14-DSL executor 도 본 응답에는
    pass/fail 카운트를 노출하지 않는다 (HTML 리포트가 artifacts 안에 따로
    생성되며 향후 UI 에서 노출 가능).
    """
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float


def _fetch_dify_token_from_container(
    container_name: str = "dscore.ttc.playwright",
    app_name: str = "ZeroTouch QA Brain",
    timeout_sec: float = 5.0,
) -> Optional[str]:
    """컨테이너의 Dify DB 에서 chatflow API token 을 조회해 반환.

    매 호출마다 fresh — provision 단계에서 chatflow 가 재 import 되어
    token 이 재발급되어도 자동 동기화된다. host shell env / .env 파일
    의존 없이 Recording UI 호출 경로가 항상 최신 token 을 사용 (portability).

    실패 (컨테이너 미실행 / docker CLI 부재 / DB 응답 비정상) 는 None
    반환. 호출 측은 기존 env (사용자가 export 했을 수도 있는 값) 그대로
    사용하도록 graceful degrade.
    """
    sql = (
        "SELECT t.token FROM api_tokens t "
        "JOIN apps a ON t.app_id = a.id "
        f"WHERE a.name = '{app_name}' "
        "ORDER BY t.created_at DESC LIMIT 1"
    )
    cmd = [
        "docker", "exec", container_name,
        "bash", "-lc",
        f"PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d dify "
        f"-t -A -c \"{sql}\"",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    token = (result.stdout or "").strip()
    return token if token.startswith("app-") else None


def _resolve_venv_py(venv_py: str | None) -> str:
    return venv_py or os.environ.get("RECORDING_VENV_PY") or sys.executable


def _dump_play_log(cwd: str, log_name: str, cmd: list[str], stdout: str, stderr: str,
                   returncode: int, elapsed_ms: float) -> None:
    """subprocess 의 stdout/stderr 를 세션 디렉토리에 떨어뜨려 healer/executor
    내부 동작을 사후 추적 가능하게 한다. 데몬 log 에는 안 들어가는 자식 프로세스
    출력의 유일한 보존 경로 — 시나리오와 실제 액션 사이 연결고리.

    실패는 silent — 본 dump 가 막히면 시나리오 결과 자체엔 영향 없음.
    """
    try:
        path = Path(cwd) / log_name
        with path.open("w", encoding="utf-8") as f:
            f.write(f"# cmd: {' '.join(cmd)}\n")
            f.write(f"# returncode: {returncode}\n")
            f.write(f"# elapsed_ms: {elapsed_ms:.0f}\n")
            f.write("# ── stdout ──────────────────────────────────────\n")
            f.write(stdout or "(empty)\n")
            if not (stdout or "").endswith("\n"):
                f.write("\n")
            f.write("# ── stderr ──────────────────────────────────────\n")
            f.write(stderr or "(empty)\n")
    except OSError as e:
        log.warning("[play-log] dump 실패 (%s): %s", cwd, e)


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    env: dict | None,
    timeout_sec: int,
    started: float,
    log_name: str = "play.log",
) -> PlayResult:
    """공용 subprocess 실행 + PlayResult 변환.

    실시간 진행 로그 (P2) 를 위해 ``subprocess.Popen`` 으로 실행하며
    stdout/stderr 를 ``log_name`` 파일에 *직접* append 한다. UI 의 1초 폴링
    (``/recording/sessions/{sid}/play-log/tail``) 이 그 파일을 tail 해 실시간
    표시. ``capture_output=True`` 로 모았다 종료 후 dump 하던 옛 흐름은 사용자에게
    실행 중 빈 화면을 보였음 (사용자 보고).

    PYTHONUNBUFFERED=1 을 env 에 강제 — Python subprocess 가 줄 단위 flush 해야
    파일에 즉시 쓰임. 미설정 시 4KB 블록 단위 버퍼링으로 폴링이 늦게 잡힘.
    """
    log_path = Path(cwd) / log_name
    # 기존 dump 와 동일한 헤더 + 라이브 로그 placeholder. 종료 후 footer 는 추가 append.
    try:
        with log_path.open("w", encoding="utf-8") as f:
            f.write(f"# cmd: {' '.join(cmd)}\n")
            f.write("# ── stdout/stderr (live) ──────────────────────\n")
    except OSError as e:
        log.warning("[play-log] 헤더 작성 실패 (%s): %s", cwd, e)

    # PYTHONUNBUFFERED — child Python 의 줄 버퍼 flush 보장.
    sub_env = dict(env) if env is not None else os.environ.copy()
    sub_env.setdefault("PYTHONUNBUFFERED", "1")

    returncode = -1
    timed_out = False
    try:
        with log_path.open("ab") as logf:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=sub_env,
                stdout=logf, stderr=subprocess.STDOUT,
            )
            try:
                returncode = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait(timeout=10)
                returncode = -1
    except FileNotFoundError as e:
        raise ReplayProxyError(f"python 호출 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    # Footer — returncode/elapsed.
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n# returncode: {returncode}\n")
            f.write(f"# elapsed_ms: {elapsed_ms:.0f}\n")
    except OSError as e:
        log.warning("[play-log] footer 작성 실패 (%s): %s", cwd, e)

    # 호환 — 기존 호출자가 PlayResult.stdout 를 참조하므로 파일 전체를 채워둔다.
    try:
        full_log = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        full_log = ""

    if timed_out:
        raise ReplayProxyError(
            f"play 가 {timeout_sec}s 안에 끝나지 않았습니다 "
            f"(elapsed={elapsed_ms:.0f}ms). 부분 출력은 {log_name} 참조."
        )

    return PlayResult(
        returncode=returncode,
        stdout=full_log,
        stderr="",  # stderr 는 stdout 으로 합쳐짐 (subprocess.STDOUT)
        elapsed_ms=elapsed_ms,
    )


def run_codegen_replay(
    *,
    host_session_dir: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    venv_py: str | None = None,
    prefer_annotated: bool = True,
    auth_profile_override: Optional[str] = None,
    headed: bool = True,
    slow_mo_ms: Optional[int] = None,
) -> PlayResult:
    """codegen 원본 ``original.py`` 를 호스트에서 그대로 실행 (기본 headed).

    내부적으로는 ``recording_service.codegen_trace_wrapper`` 를 통해 실행하여
    Playwright tracing 을 자동 주입한다. subprocess 종료 후 ``trace.zip`` 을
    파싱해 LLM 모드와 같은 형식의 ``codegen_run_log.jsonl`` +
    ``codegen_screenshots/`` 를 생성 — Run Log 카드가 두 모드를 동등하게
    노출할 수 있게 한다.

    Args:
        host_session_dir: 호스트 측 세션 디렉토리 — ``original.py`` 위치.
        timeout_sec: subprocess timeout (기본 300s).
        venv_py: 인터프리터 경로 override.
        prefer_annotated: True 이고 ``original_annotated.py`` 가 있으면 그걸 우선
            실행 (T-H 정적 hover 주입본). 기본 True.
        auth_profile_override: R-Plus Play 호출 시 사용자가 명시한 프로파일.
            ``None`` 이면 metadata 사용, ``""`` 이면 인증 없이, 이름이면 override.
        headed: False 면 codegen 스크립트의 ``launch()`` headless 인자를 강제로
            True 로 patch (CODEGEN_HEADLESS=1 env 로 wrapper 에 전달).
    """
    sess_dir = Path(host_session_dir)
    annotated = sess_dir / "original_annotated.py"
    if prefer_annotated and annotated.is_file():
        script_name = "original_annotated.py"
    else:
        script_name = "original.py"
    script = sess_dir / script_name
    if not script.is_file():
        raise ReplayProxyError(f"실행 대상 .py 없음: {script}")

    py = _resolve_venv_py(venv_py)
    cmd = [py, "-m", "recording_service.codegen_trace_wrapper"]

    # 래퍼는 CODEGEN_SESSION_DIR / CODEGEN_SCRIPT env 로 실행 대상을 받는다.
    # auth-profile env (P4.3) 도 동일하게 전달.
    storage_path, fingerprint_env, profile_name = _resolve_auth_for_replay(
        host_session_dir, override=auth_profile_override,
    )
    env = os.environ.copy()
    env["CODEGEN_SESSION_DIR"] = str(sess_dir)
    env["CODEGEN_SCRIPT"] = script_name
    # PYTHONPATH 주입 — 래퍼는 recording_service 패키지에 속하므로 import 가능 보장.
    project_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = (
        project_root + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    )
    if storage_path:
        env["AUTH_STORAGE_STATE_IN"] = storage_path
        if fingerprint_env:
            env.update(fingerprint_env)
        log.info(
            "[play-codegen] auth-profile=%s storage=%s",
            profile_name, storage_path,
        )
    if not headed:
        # codegen wrapper 가 BrowserType.launch() 의 headless 인자를 강제 True 로 monkey-patch.
        env["CODEGEN_HEADLESS"] = "1"
    else:
        # Playwright 기본값이 headless=True 라, 사용자 스크립트가 launch() 인자를
        # 안 주면 headed 체크박스에도 화면이 안 뜬다. wrapper 가 headless=False 를
        # 강제 주입하도록 별도 env 로 신호.
        env["CODEGEN_HEADED"] = "1"
    if slow_mo_ms and slow_mo_ms > 0:
        # wrapper 가 BrowserType.launch() kwargs 에 slow_mo 를 주입한다.
        env["CODEGEN_SLOW_MO_MS"] = str(int(slow_mo_ms))

    log.info(
        "[play-codegen] %s (script=%s, traced)", " ".join(cmd), script_name
    )
    result = _run_subprocess(
        cmd, cwd=host_session_dir, env=env,
        timeout_sec=timeout_sec, started=time.time(),
        log_name="play-codegen.log",
    )

    # subprocess 종료 후 trace.zip → codegen_run_log.jsonl + 스크린샷 변환.
    # 파싱 실패는 silent — codegen 재생 자체 결과(returncode/stdout)에 영향 없음.
    try:
        from recording_service import trace_parser
        trace_zip = sess_dir / "trace.zip"
        if trace_zip.is_file():
            n = trace_parser.parse_trace(
                trace_zip,
                out_run_log=sess_dir / "codegen_run_log.jsonl",
                out_screenshots_dir=sess_dir / "codegen_screenshots",
            )
            log.info("[play-codegen] trace 파싱 완료: %d step", n)
        else:
            log.info("[play-codegen] trace.zip 없음 — 파싱 스킵")
    except Exception as e:  # noqa: BLE001
        log.warning("[play-codegen] trace 파싱 실패 (무시하고 계속): %s", e)

    return result


def run_llm_play(
    *,
    host_session_dir: str,
    project_root: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    venv_py: str | None = None,
    auth_profile_override: Optional[str] = None,
    headed: bool = True,
    slow_mo_ms: Optional[int] = None,
) -> PlayResult:
    """변환된 14-DSL ``scenario.json`` 을 zero_touch_qa executor 로 실행 (기본 headed).

    Args:
        host_session_dir: 호스트 측 세션 디렉토리 — ``scenario.json`` 위치 +
            artifacts 산출물도 같은 폴더로 떨어진다 (ARTIFACTS_DIR).
        project_root: zero_touch_qa 패키지가 있는 프로젝트 루트 — PYTHONPATH 주입.
        timeout_sec: subprocess timeout (기본 300s).
        venv_py: 인터프리터 경로 override.
        auth_profile_override: R-Plus Play 호출 시 사용자가 명시한 프로파일.
            ``None`` 이면 metadata 사용, ``""`` 이면 인증 없이, 이름이면 override.
        headed: False 면 ``--headless`` 플래그를 executor 에 전달.
    """
    scenario = Path(host_session_dir) / "scenario.json"
    if not scenario.is_file():
        raise ReplayProxyError(f"scenario.json 없음: {scenario}")

    py = _resolve_venv_py(venv_py)
    cmd = [
        py, "-m", "zero_touch_qa",
        "--mode", "execute",
        "--scenario", str(scenario),
    ]
    if not headed:
        cmd.append("--headless")
    if slow_mo_ms and slow_mo_ms > 0:
        cmd += ["--slow-mo", str(int(slow_mo_ms))]

    # P4.2 — auth-profile 자동 매칭. 메타에 auth_profile 이 있으면 verify 통과 후
    # ``--storage-state-in <path>`` 인자 + fingerprint env 주입.
    storage_path, fingerprint_env, profile_name = _resolve_auth_for_replay(
        host_session_dir, override=auth_profile_override,
    )
    if storage_path:
        cmd += ["--storage-state-in", storage_path]
        log.info(
            "[play-llm] auth-profile=%s storage=%s",
            profile_name, storage_path,
        )

    env = os.environ.copy()
    env["PYTHONPATH"] = project_root + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    env["ARTIFACTS_DIR"] = host_session_dir
    # Dify chatflow token 자동 주입 — 컨테이너 DB 에서 매 호출마다 fresh fetch.
    # provision 으로 token 재발급되어도 (또는 새 컴퓨터에 이미지 배포 후 첫 실행
    # 시점에도) 사용자 export 없이 zero_touch_qa 의 healing 경로가 정상 동작.
    fetched_token = _fetch_dify_token_from_container()
    if fetched_token:
        env["DIFY_API_KEY"] = fetched_token
    if fingerprint_env:
        env.update(fingerprint_env)
    log.info("[play-llm] %s (cwd=%s)", " ".join(cmd), host_session_dir)
    return _run_subprocess(
        cmd, cwd=host_session_dir, env=env,
        timeout_sec=timeout_sec, started=time.time(),
        log_name="play-llm.log",
    )
