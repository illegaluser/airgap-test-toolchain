"""Session ID / state management (Phase R-MVP TR.1).

A session lives both in memory and in a persistence directory (storage.py).
This module owns only the in-memory registry; persistence is delegated to
storage.py.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


# Session state machine
STATE_PENDING = "pending"            # Right after /start, before codegen begins
STATE_RECORDING = "recording"        # codegen subprocess is running
STATE_CONVERTING = "converting"      # docker exec --convert-only is in progress
STATE_DONE = "done"                  # scenario.json has been saved
STATE_ERROR = "error"                # Failure at any stage


@dataclass
class Session:
    """A single recording session."""

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
        # Add client-friendly ISO timestamps
        d["created_at_iso"] = _iso(self.created_at)
        d["started_at_iso"] = _iso(self.started_at)
        d["ended_at_iso"] = _iso(self.ended_at)
        return d


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


class SessionRegistry:
    """Process-local session store. Thread-safe.

    Under a multi-worker uvicorn each worker has its own registry, so
    R-MVP assumes a single worker (`--workers 1`).
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
        """For tests."""
        with self._lock:
            self._sessions.clear()
