"""세션 ID·상태 관리 (Phase R-MVP TR.1).

세션은 메모리 + 영속화 디렉토리 (storage.py) 에 동시 존재한다. 본 모듈은
in-memory 레지스트리만 담당. 영속화는 storage.py 위임.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


# 세션 상태 머신
STATE_PENDING = "pending"            # /start 직후, codegen 시작 전
STATE_RECORDING = "recording"        # codegen subprocess 실행 중
STATE_CONVERTING = "converting"      # docker exec --convert-only 진행 중
STATE_DONE = "done"                  # scenario.json 저장 완료
STATE_ERROR = "error"                # 어느 단계든 실패


@dataclass
class Session:
    """단일 녹화 세션."""

    id: str
    target_url: str
    state: str
    created_at: float
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    planning_doc_ref: Optional[str] = None
    pid: Optional[int] = None
    action_count: int = 0
    error: Optional[str] = None
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # 클라이언트 친화 ISO 시간 추가
        d["created_at_iso"] = _iso(self.created_at)
        d["started_at_iso"] = _iso(self.started_at)
        d["ended_at_iso"] = _iso(self.ended_at)
        return d


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


class SessionRegistry:
    """프로세스-로컬 세션 저장소. thread-safe.

    multi-worker uvicorn 환경에서는 각 worker 가 자기 레지스트리를 가지므로
    R-MVP 단계는 단일 worker (`--workers 1`) 가정.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, target_url: str, planning_doc_ref: Optional[str] = None) -> Session:
        sid = uuid.uuid4().hex[:12]
        sess = Session(
            id=sid,
            target_url=target_url,
            state=STATE_PENDING,
            created_at=time.time(),
            planning_doc_ref=planning_doc_ref,
        )
        with self._lock:
            self._sessions[sid] = sess
        return sess

    def get(self, sid: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(sid)

    def list(self) -> list[Session]:
        with self._lock:
            return sorted(
                list(self._sessions.values()),
                key=lambda s: s.created_at,
                reverse=True,
            )

    def update(self, sid: str, **kwargs) -> Optional[Session]:
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is None:
                return None
            for k, v in kwargs.items():
                if hasattr(sess, k):
                    setattr(sess, k, v)
            return sess

    def delete(self, sid: str) -> bool:
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def clear(self) -> None:
        """테스트용."""
        with self._lock:
            self._sessions.clear()
