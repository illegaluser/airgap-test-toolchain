"""
In-process rate limiter (REQ-007).

Sliding window counters keyed by (route, client_ip). NOT distributed —
single-instance only; REQ-007 notes a Redis-backed version is a future
requirement.
"""

import time
from collections import deque


# Shared mutable state — one deque of timestamps per key.
_BUCKETS: dict = {}


def allow_request(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if a new hit on `key` is within `limit` in the sliding window.

    INTENTIONAL ISSUE (MAJOR): thread-safety. Two concurrent requests can both
    read `len(bucket) < limit` as True and both append — exceeding the limit.
    A lock is needed around the read-modify-write. SonarQube python:S5247
    (non-thread-safe) or similar profile-dependent rule.
    """
    now = time.time()
    bucket = _BUCKETS.setdefault(key, deque())
    # Evict old entries outside window.
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)  # ← race: two concurrent threads can both reach here.
    return True


def reset(key: str = None) -> None:
    """Test helper — clear one key or all buckets."""
    if key is None:
        _BUCKETS.clear()
    else:
        _BUCKETS.pop(key, None)
