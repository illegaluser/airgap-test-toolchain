"""컨테이너 CLI 위임 변환 (Phase R-MVP TR.3 + 2026-05-11 docker-cp 재설계).

설계: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"T0.3" / §"TR.3"

본래 TR.8 결정은 호스트와 컨테이너가 동일 경로를 *bind mount* 로 공유 (`-v
$HOST_RECORDINGS_DIR:/recordings:rw`) 하고, 그 경로 위에 호스트가 업로드한
``original.py`` 를 컨테이너가 직접 읽는 구조였다.

문제: 호스트가 어디서 도느냐 (Mac native / Windows native / WSL2) 에 따라
``$HOST_RECORDINGS_DIR`` 가 다른 파일시스템을 가리키게 되고, 컨테이너의 mount
source 와 안 맞으면 변환이 silent 실패한다 (컨테이너가 호스트 파일을 못 봄).
즉 mount 정합이 무너지면 `LLM 적용 코드 실행` 이 통째로 비활성으로 떨어진다 —
사용자가 UI 에서 본 진짜 증상.

재설계 (2026-05-11): mount 의존을 끊고 ``docker cp`` 로 명시적 in/out:

    1. 컨테이너 안 임시 scratch (`/tmp/recording-convert/<sid>`) 디렉토리 생성
    2. ``docker cp <host_session_dir>/original.py <ctn>:<scratch>/original.py``
    3. ``docker exec ... -e ARTIFACTS_DIR=<scratch> --file <scratch>/original.py``
    4. ``docker cp <ctn>:<scratch>/scenario.json <host_scenario_path>``
    5. ``docker exec rm -rf <scratch>`` (best-effort)

이로써 호스트의 RECORDING_HOST_ROOT 가 어떤 파일시스템에 있어도 동작한다.
build.sh 의 ``-v ...:/recordings:rw`` mount 가 있어도 무시 — 새 흐름은 그것을
사용 안 함.

성공 조건: docker exec exit 0 + 호스트 ``host_scenario_path`` 에 scenario.json
가 떨어진 상태. 실패 시 stderr 그대로 호출자에게 전달.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_CONTAINER_NAME = os.environ.get(
    "RECORDING_CONTAINER_NAME", "dscore.ttc.playwright",
)
DEFAULT_DOCKER_EXEC_TIMEOUT_SEC = int(
    os.environ.get("RECORDING_CONVERT_TIMEOUT_SEC", "60")
)
# docker cp / mkdir / rm 같은 보조 호출은 별도 짧은 timeout.
_AUX_DOCKER_TIMEOUT_SEC = 30


class ConverterProxyError(RuntimeError):
    """변환 단계의 명시적 에러."""


@dataclass
class ConvertResult:
    """변환 호출 결과 + 호스트 측 산출물 존재 여부."""

    returncode: int
    stdout: str
    stderr: str
    scenario_path: str
    scenario_exists: bool
    elapsed_ms: float


def is_docker_available() -> bool:
    return shutil.which("docker") is not None


def _docker_run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    """docker subprocess 실행 + 명시적 timeout. 실패 시 ConverterProxyError."""
    try:
        return subprocess.run(
            args, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ConverterProxyError(
            f"docker 호출 timeout ({timeout}s): {' '.join(args)}"
        ) from e
    except (OSError, subprocess.SubprocessError) as e:
        raise ConverterProxyError(f"docker 호출 실패: {e}") from e


def _cleanup_scratch(container_name: str, scratch: str) -> None:
    """컨테이너 안 임시 scratch 정리. best-effort — 실패해도 변환 결과에 영향 X."""
    try:
        subprocess.run(
            ["docker", "exec", container_name, "rm", "-rf", scratch],
            capture_output=True,
            timeout=_AUX_DOCKER_TIMEOUT_SEC,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass


def run_convert(
    *,
    host_session_dir: str,
    host_scenario_path: str,
    container_name: str = DEFAULT_CONTAINER_NAME,
    timeout_sec: int = DEFAULT_DOCKER_EXEC_TIMEOUT_SEC,
) -> ConvertResult:
    """docker cp 기반 변환 — 호스트/컨테이너 mount 무관.

    Args:
        host_session_dir: 호스트의 세션 디렉토리. 그 안 ``original.py`` 가
            input. 없으면 ConverterProxyError.
        host_scenario_path: 변환 결과 ``scenario.json`` 을 떨어뜨릴 호스트
            파일 경로. 부모 디렉토리는 사전에 존재해야 함 (caller 책임).
        container_name: docker 컨테이너 이름 (build.sh CONTAINER_NAME 과 일치).
        timeout_sec: 메인 변환 단계 (`docker exec ... zero_touch_qa --convert`)
            timeout. 보조 호출 (mkdir / cp / rm) 은 별도 짧은 timeout.

    Raises:
        ConverterProxyError: docker 미설치 / aux 단계 실패 / main exec timeout.
    """
    if not is_docker_available():
        raise ConverterProxyError(
            "docker 실행 파일을 찾을 수 없습니다. 호스트 PATH 를 확인하세요."
        )

    host_session = Path(host_session_dir)
    host_original = host_session / "original.py"
    if not host_original.is_file():
        raise ConverterProxyError(f"original.py 없음: {host_original}")

    sid = host_session.name
    # 컨테이너 안 scratch — 매 호출마다 격리.
    scratch = f"/tmp/recording-convert/{sid}"

    started = time.time()

    # 1. mkdir scratch.
    mkdir_proc = _docker_run(
        ["docker", "exec", container_name, "mkdir", "-p", scratch],
        timeout=_AUX_DOCKER_TIMEOUT_SEC,
    )
    if mkdir_proc.returncode != 0:
        raise ConverterProxyError(
            f"scratch dir 생성 실패: rc={mkdir_proc.returncode} "
            f"stderr={mkdir_proc.stderr.decode('utf-8', errors='replace')[:300]}"
        )

    try:
        # 2. docker cp original.py → 컨테이너 scratch.
        cp_in = _docker_run(
            [
                "docker", "cp",
                str(host_original),
                f"{container_name}:{scratch}/original.py",
            ],
            timeout=_AUX_DOCKER_TIMEOUT_SEC,
        )
        if cp_in.returncode != 0:
            raise ConverterProxyError(
                f"docker cp original.py 실패: rc={cp_in.returncode} "
                f"stderr={cp_in.stderr.decode('utf-8', errors='replace')[:300]}"
            )

        # 3. 메인 변환 — exec.
        cmd = [
            "docker", "exec",
            "-w", "/opt",
            "-e", f"ARTIFACTS_DIR={scratch}",
            container_name,
            "/opt/qa-venv/bin/python", "-m", "zero_touch_qa",
            "--mode", "convert",
            "--convert-only",
            "--file", f"{scratch}/original.py",
        ]
        log.info("[convert-proxy] %s", " ".join(cmd))
        proc = _docker_run(cmd, timeout=timeout_sec)

        elapsed_ms = (time.time() - started) * 1000
        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

        # 4. 성공이면 docker cp scenario.json → 호스트.
        scenario_exists = False
        if proc.returncode == 0:
            cp_out = _docker_run(
                [
                    "docker", "cp",
                    f"{container_name}:{scratch}/scenario.json",
                    host_scenario_path,
                ],
                timeout=_AUX_DOCKER_TIMEOUT_SEC,
            )
            if cp_out.returncode == 0 and Path(host_scenario_path).is_file():
                scenario_exists = True
            else:
                log.warning(
                    "[convert-proxy] scenario.json copy-out 실패 (rc=%s): %s",
                    cp_out.returncode,
                    cp_out.stderr.decode("utf-8", errors="replace")[:300],
                )

        return ConvertResult(
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            scenario_path=host_scenario_path,
            scenario_exists=scenario_exists,
            elapsed_ms=elapsed_ms,
        )
    finally:
        # 5. cleanup. best-effort.
        _cleanup_scratch(container_name, scratch)
