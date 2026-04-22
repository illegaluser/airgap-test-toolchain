"""
eval_runner.dataset — Golden dataset 로딩 + 메타 수집 + 경로 탐색

Phase 4.2 Q4 에서 test_runner.py 로부터 분리. 다른 모듈(policy, scoring,
state, runner) 이 import 해 사용한다.

공개 심볼:
- load_dataset() — CSV → conversation 단위 list of list[turn_dict]
- GOLDEN_CSV_PATH — 환경 / fallback 에 따라 결정된 현재 경로
- _is_blank_value / _turn_sort_key — CSV 정규화 헬퍼
- _collect_dataset_meta — summary.json aggregate.dataset 용 메타
"""

from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path

import pandas as pd


# ============================================================================
# 경로 상수 + 기본값 fallback
# ============================================================================

MODULE_ROOT = Path(__file__).resolve().parent  # eval_runner/ 디렉터리

DEFAULT_GOLDEN_PATHS = [
    MODULE_ROOT / "data" / "golden.csv",  # 개발 환경
    Path("/var/knowledges/eval/data/golden.csv"),  # Jenkins 볼륨
    Path("/var/jenkins_home/knowledges/eval/data/golden.csv"),  # Jenkins 홈
    Path("/app/data/golden.csv"),  # Docker 앱
]


def _resolve_existing_path(env_value: str | None, fallback_paths) -> Path:
    """
    환경변수 우선, 아니면 fallback 리스트에서 실존 첫 파일.
    모두 실패하면 fallback 첫 경로 반환 (후속 오류 메시지가 경로를 알려줄 수 있게).
    """
    if env_value:
        return Path(env_value).expanduser()
    for path in fallback_paths:
        if path.exists():
            return path
    return Path(fallback_paths[0])


GOLDEN_CSV_PATH = _resolve_existing_path(
    os.environ.get("GOLDEN_CSV_PATH"),
    DEFAULT_GOLDEN_PATHS,
)


# ============================================================================
# 정규화 헬퍼
# ============================================================================

def _is_blank_value(value) -> bool:
    """CSV None/NaN/공백 문자열을 모두 공백으로 통일."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _turn_sort_key(value):
    """turn_id 정렬 키 — 숫자 우선, 실패 시 문자열, None 은 맨 뒤."""
    if value is None:
        return (1, 0)
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (0, str(value))


# ============================================================================
# 로딩
# ============================================================================

def load_dataset():
    """
    golden.csv 를 conversation 단위로 그룹화.
    conversation_id 가 있으면 멀티턴으로 묶고, 없으면 각 row 를 단일턴 대화 1개로.
    """
    if not GOLDEN_CSV_PATH.exists():
        raise FileNotFoundError(f"Evaluation dataset not found at {GOLDEN_CSV_PATH}")

    df = pd.read_csv(GOLDEN_CSV_PATH)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if "conversation_id" not in df.columns:
        return [[record] for record in records]

    grouped_conversations: dict = {}
    grouped_order: list = []
    single_turn_conversations: list = []

    for record in records:
        conversation_id = record.get("conversation_id")
        if not _is_blank_value(conversation_id):
            key = str(conversation_id)
            if key not in grouped_conversations:
                grouped_conversations[key] = []
                grouped_order.append(key)
            grouped_conversations[key].append(record)
        else:
            record["conversation_id"] = None
            single_turn_conversations.append([record])

    conversations: list = []
    for key in grouped_order:
        turns = grouped_conversations[key]
        if "turn_id" in df.columns:
            turns = sorted(turns, key=lambda turn: _turn_sort_key(turn.get("turn_id")))
        conversations.append(turns)

    conversations.extend(single_turn_conversations)
    return conversations


# ============================================================================
# Phase 3.2 Q3 — Dataset 메타 (drift 추적)
# ============================================================================

def _collect_dataset_meta() -> dict:
    """
    summary.json aggregate.dataset 에 저장할 메타.
    sha256 (hex 64) + rows (CSV line count - 헤더) + mtime (ISO8601 UTC).
    파일 부재 시 값은 None — 예외 대신 graceful fallback.
    """
    meta: dict = {
        "path": str(GOLDEN_CSV_PATH),
        "sha256": None,
        "rows": None,
        "mtime": None,
    }
    try:
        if GOLDEN_CSV_PATH.exists():
            data = GOLDEN_CSV_PATH.read_bytes()
            meta["sha256"] = hashlib.sha256(data).hexdigest()
            lines = [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]
            meta["rows"] = max(0, len(lines) - 1)
            mtime_ts = GOLDEN_CSV_PATH.stat().st_mtime
            meta["mtime"] = datetime.datetime.fromtimestamp(mtime_ts, datetime.timezone.utc).isoformat()
    except Exception:
        pass
    return meta
