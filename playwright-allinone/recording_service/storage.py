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


def session_dir(session_id: str) -> Path:
    """`<host_root>/<session_id>/` 경로 반환 + 디렉토리 생성."""
    p = host_root() / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def container_path_for(session_id: str) -> str:
    """컨테이너 내부에서 본 경로. docker exec 호출 시 사용.

    bind-mount 가 `/data/recordings` 로 가정 (T0.3 계약).
    """
    base = os.environ.get("RECORDING_CONTAINER_ROOT", "/data/recordings")
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
