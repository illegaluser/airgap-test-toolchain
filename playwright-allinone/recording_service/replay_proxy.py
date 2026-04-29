"""TR.7 — Replay 검증 (R-Plus).

설계: PLAN_GROUNDING_RECORDING_AGENT.md §"TR.7"

녹화된 14-DSL 시나리오를 컨테이너 측 executor 로 재실행. converter_proxy 와
동일한 docker exec 위임 패턴 — 호스트는 명령만 보내고 결과(run_log.json)는
공유 디렉토리에서 읽는다.

호출:
    docker exec -e ARTIFACTS_DIR=<container_session_dir> <container_name> \
        python -m zero_touch_qa --mode execute --headless \
        --scenario <container_session_dir>/scenario.json
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_CONTAINER_NAME = os.environ.get(
    "RECORDING_CONTAINER_NAME", "dscore.ttc.playwright",
)
DEFAULT_REPLAY_TIMEOUT_SEC = int(
    os.environ.get("RECORDING_REPLAY_TIMEOUT_SEC", "300")
)


class ReplayProxyError(RuntimeError):
    """replay 단계의 명시적 에러."""


@dataclass
class ReplayResult:
    returncode: int
    stdout: str
    stderr: str
    run_log_path: str
    run_log_exists: bool
    pass_count: int
    fail_count: int
    healed_count: int
    elapsed_ms: float


def is_docker_available() -> bool:
    return shutil.which("docker") is not None


def run_replay(
    *,
    container_name: str = DEFAULT_CONTAINER_NAME,
    container_session_dir: str,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
    host_session_dir: Optional[str] = None,
) -> ReplayResult:
    """docker exec 로 컨테이너 측 executor 재실행.

    Args:
        container_name: 컨테이너 이름 (build.sh CONTAINER_NAME)
        container_session_dir: 컨테이너 측 세션 디렉토리 (`/recordings/<id>`).
            ARTIFACTS_DIR 로 사용 + scenario 위치도 같음.
        timeout_sec: docker exec 단일 호출 timeout (replay 는 변환보다 길게,
            기본 300s)
        host_session_dir: 호스트 측 세션 디렉토리 (`run_log.json` 등 결과 파일
            확인용)
    """
    if not is_docker_available():
        raise ReplayProxyError(
            "docker 실행 파일을 찾을 수 없습니다. 호스트 PATH 확인."
        )

    # converter_proxy 와 동일 패턴 — `-w /opt` 로 PYTHONPATH 자동 해결,
    # qa-venv python 명시. system python (`/usr/local/bin/python`) 은
    # `/opt` 가 path 에 없어 zero_touch_qa import 실패.
    cmd = [
        "docker", "exec",
        "-w", "/opt",
        "-e", f"ARTIFACTS_DIR={container_session_dir}",
        container_name,
        "/opt/qa-venv/bin/python", "-m", "zero_touch_qa",
        "--mode", "execute",
        "--headless",
        "--scenario", f"{container_session_dir}/scenario.json",
    ]
    log.info("[replay-proxy] %s", " ".join(cmd))

    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired as e:
        elapsed = (time.time() - started) * 1000
        raise ReplayProxyError(
            f"docker exec replay 가 {timeout_sec}s 안에 끝나지 않았습니다 (elapsed={elapsed:.0f}ms)."
        ) from e
    except FileNotFoundError as e:
        raise ReplayProxyError(f"docker 호출 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    # run_log.json 카운팅 — host_session_dir 기준
    pass_count = fail_count = healed_count = 0
    run_log_exists = False
    run_log_path = ""
    if host_session_dir:
        rlp = os.path.join(host_session_dir, "run_log.json")
        run_log_path = rlp
        if os.path.isfile(rlp):
            run_log_exists = True
            try:
                data = json.loads(open(rlp, encoding="utf-8").read())
                if isinstance(data, list):
                    for entry in data:
                        status = (entry.get("status") or "").lower()
                        if status == "pass":
                            pass_count += 1
                        elif status == "fail":
                            fail_count += 1
                        elif status in ("healed", "heal"):
                            healed_count += 1
            except (OSError, json.JSONDecodeError) as e:
                log.warning("[replay-proxy] run_log.json 파싱 실패: %s", e)

    return ReplayResult(
        returncode=proc.returncode,
        stdout=stdout, stderr=stderr,
        run_log_path=run_log_path,
        run_log_exists=run_log_exists,
        pass_count=pass_count,
        fail_count=fail_count,
        healed_count=healed_count,
        elapsed_ms=elapsed_ms,
    )
