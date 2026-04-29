"""Container-CLI delegated conversion (Phase R-MVP TR.3).

Design: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"T0.3" / §"TR.3"

The host side does not import converter.py. Conversion is delegated to the
container CLI:

    docker exec -e ARTIFACTS_DIR=<container_session_dir> <container_name> \
        python -m zero_touch_qa --mode convert --convert-only \
        --file <container_session_dir>/original.py

Success: exit 0 + scenario.json appears in the same directory.
Failure: stderr is passed through to the caller.

Live operation needs the host recordings directory to be bind-mounted into
docker run in build.sh (TR.8). At the TR.3 stage we just unit-test the flow.
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
    """Explicit error for the conversion stage."""


@dataclass
class ConvertResult:
    """docker exec run result + whether scenario.json exists."""

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
    """Run the container-side conversion via docker exec.

    Args:
        container_name: docker container name (matches build.sh CONTAINER_NAME)
        container_session_dir: session directory as seen from inside the container
            (e.g. `/data/recordings/<id>`). Used for both ARTIFACTS_DIR and --file.
        timeout_sec: timeout for a single docker exec call.
        host_scenario_path: used for verification. The host-side path to the
            scenario.json the container wrote into ARTIFACTS_DIR. With a healthy
            mount, this is the same file.

    Raises:
        ConverterProxyError: docker missing / timeout
    """
    if not is_docker_available():
        raise ConverterProxyError(
            "docker executable not found. Check the host PATH."
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
            f"docker exec conversion did not finish within {timeout_sec}s (elapsed={elapsed:.0f}ms)."
        ) from e
    except FileNotFoundError as e:
        raise ConverterProxyError(f"docker invocation failed: {e}") from e

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
