"""세션 영속화 (Phase R-MVP TR.1 골격, TR.8 에서 보강).

호스트의 `~/.dscore.ttc.playwright-agent/recordings/<id>/` 가 진실의 원천.
컨테이너는 동일 디렉토리를 `/data/recordings/<id>/` 로 마운트.

본 모듈은 디렉토리 생성·경로 계산·간단한 metadata 저장만 담당.
TR.8 에서 마운트 검증·session 목록 동기화 보강.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional


DEFAULT_HOST_RECORDINGS_DIR = os.path.expanduser(
    "~/.dscore.ttc.playwright-agent/recordings"
)


def host_root() -> Path:
    """호스트 측 recordings 루트. env `RECORDING_HOST_ROOT` override 가능."""
    return Path(os.environ.get("RECORDING_HOST_ROOT", DEFAULT_HOST_RECORDINGS_DIR))


def discoveries_root() -> Path:
    """호스트 측 Discover URLs 결과 루트. env `DISCOVERY_HOST_ROOT` override 가능.

    `host_root()` 와 분리 — recordings 루트를 그대로 쓰면
    `recordings/discoveries/...` 로 잘못 저장되므로 별도 헬퍼.
    """
    raw = os.environ.get("DISCOVERY_HOST_ROOT")
    if raw:
        return Path(raw).expanduser()
    base = os.environ.get("DSCORE_AGENT_DIR", "~/.dscore.ttc.playwright-agent")
    return Path(base).expanduser() / "discoveries"


def session_dir(session_id: str) -> Path:
    """`<host_root>/<session_id>/` 경로 반환 + 디렉토리 생성."""
    p = host_root() / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def container_path_for(session_id: str) -> str:
    """컨테이너 내부에서 본 경로. docker exec 호출 시 사용.

    TR.8 결정: `/data` named volume 위에 nested bind mount 를 하면 docker 보장이
    약하므로, **별도 마운트 포인트 `/recordings`** 채택. build.sh 의 docker run
    에 `-v <host_recordings>:/recordings:rw` 추가.

    env `RECORDING_CONTAINER_ROOT` 로 override 가능 (운영 환경별 조정용).
    """
    base = os.environ.get("RECORDING_CONTAINER_ROOT", "/recordings")
    return f"{base}/{session_id}"


def save_metadata(session_id: str, meta: dict) -> Path:
    """`<session_dir>/metadata.json` 저장."""
    d = session_dir(session_id)
    path = d / "metadata.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_metadata(session_id: str) -> Optional[dict]:
    p = session_dir(session_id) / "metadata.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def scenario_path(session_id: str) -> Path:
    """변환 결과 scenario.json 의 호스트측 경로."""
    return session_dir(session_id) / "scenario.json"


def original_py_path(session_id: str) -> Path:
    """codegen 산출 원본 .py 의 호스트측 경로."""
    return session_dir(session_id) / "original.py"


def scenario_healed_path(session_id: str) -> Path:
    """Play with LLM 실행 후 self-healing 적용된 scenario.json (selector 치환 등)."""
    return session_dir(session_id) / "scenario.healed.json"


def play_llm_log_path(session_id: str) -> Path:
    """Play with LLM 실행 로그 (전체 본문 — 미리보기/다운로드용)."""
    return session_dir(session_id) / "play-llm.log"


def regression_py_path(session_id: str) -> Path:
    """executor 가 scenario.healed.json 으로부터 자동 생성한 회귀 테스트 .py."""
    return session_dir(session_id) / "regression_test.py"


def run_log_path(session_id: str) -> Path:
    """Play 실행 후 step 별 PASS/HEALED/FAIL 기록 jsonl."""
    return session_dir(session_id) / "run_log.jsonl"


def load_scenario(session_id: str) -> Optional[list]:
    p = scenario_path(session_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def delete_session(session_id: str) -> bool:
    """세션 디렉토리 전체 제거. 존재하지 않으면 False."""
    import shutil
    d = host_root() / session_id
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def list_session_dirs() -> list[str]:
    """호스트 영속화 루트 안의 세션 ID 목록 (디렉토리 이름).

    server 프로세스가 재시작되어도 디스크의 세션은 보존됨. UI 의 "최근 세션"
    표가 startup 시점에 디스크 세션을 흡수하도록 사용.
    """
    root = host_root()
    if not root.exists():
        return []
    return sorted(
        [p.name for p in root.iterdir() if p.is_dir()],
    )
