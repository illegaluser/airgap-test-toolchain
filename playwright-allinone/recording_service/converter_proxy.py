"""컨테이너 CLI 위임 변환 (Phase R-MVP TR.3).

설계: PLAN_GROUNDING_RECORDING_AGENT.md §"T0.3" / §"TR.3"

호스트 측은 converter.py 를 import 하지 않는다. 변환은 컨테이너 CLI 에 위임:

    docker exec -e ARTIFACTS_DIR=<container_session_dir> <container_name> \
        python -m zero_touch_qa --mode convert --convert-only \
        --file <container_session_dir>/original.py

성공 조건: exit 0 + 같은 디렉토리에 scenario.json 생성됨.
실패 시 stderr 그대로 호출자에게 전달.

라이브 동작은 build.sh 의 docker run 에 호스트 recordings 디렉토리 bind mount
추가 후 (TR.8). TR.3 단계는 단위 테스트로 흐름만 검증.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_CONTAINER_NAME = os.environ.get(
    "RECORDING_CONTAINER_NAME", "dscore.ttc.playwright",
)
DEFAULT_DOCKER_EXEC_TIMEOUT_SEC = int(
    os.environ.get("RECORDING_CONVERT_TIMEOUT_SEC", "60")
)


class ConverterProxyError(RuntimeError):
    """변환 단계의 명시적 에러."""


@dataclass
class ConvertResult:
    """docker exec 실행 결과 + scenario.json 존재 여부."""

    returncode: int
    stdout: str
    stderr: str
    scenario_path: str
    scenario_exists: bool
    elapsed_ms: float


def is_docker_available() -> bool:
    return shutil.which("docker") is not None


def run_convert(
    *,
    container_name: str = DEFAULT_CONTAINER_NAME,
    container_session_dir: str,
    timeout_sec: int = DEFAULT_DOCKER_EXEC_TIMEOUT_SEC,
    host_scenario_path: Optional[str] = None,
) -> ConvertResult:
    """docker exec 로 컨테이너 측 변환 실행.

    Args:
        container_name: docker 컨테이너 이름 (build.sh CONTAINER_NAME 과 일치)
        container_session_dir: 컨테이너 시점의 세션 디렉토리 (예 `/data/recordings/<id>`).
            ARTIFACTS_DIR + --file 양쪽에 사용된다.
        timeout_sec: docker exec 단일 호출 timeout.
        host_scenario_path: 검증 시 사용. 컨테이너가 ARTIFACTS_DIR 에 쓴
            scenario.json 의 호스트 측 경로. 마운트가 정상이면 동일 파일.

    Raises:
        ConverterProxyError: docker 미설치 / timeout
    """
    if not is_docker_available():
        raise ConverterProxyError(
            "docker 실행 파일을 찾을 수 없습니다. 호스트 PATH 를 확인하세요."
        )

    cmd = [
        "docker", "exec",
        "-w", "/opt",
        "-e", f"ARTIFACTS_DIR={container_session_dir}",
        container_name,
        "/opt/qa-venv/bin/python", "-m", "zero_touch_qa",
        "--mode", "convert",
        "--convert-only",
        "--file", f"{container_session_dir}/original.py",
    ]
    log.info("[convert-proxy] %s", " ".join(cmd))

    import time
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = (time.time() - started) * 1000
        raise ConverterProxyError(
            f"docker exec 변환이 {timeout_sec}s 안에 끝나지 않았습니다 (elapsed={elapsed:.0f}ms)."
        ) from e
    except FileNotFoundError as e:
        raise ConverterProxyError(f"docker 호출 실패: {e}") from e

    elapsed_ms = (time.time() - started) * 1000
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    scenario_path = host_scenario_path or ""
    scenario_exists = bool(scenario_path) and os.path.isfile(scenario_path)

    return ConvertResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        scenario_path=scenario_path,
        scenario_exists=scenario_exists,
        elapsed_ms=elapsed_ms,
    )
