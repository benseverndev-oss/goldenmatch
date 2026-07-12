"""Bounded, TTL'd store of the last AgentSession per MCP session id.

Lazy eviction (no background thread): expired entries drop on access; when the
store exceeds ``max_sessions`` the least-recently-used entry is evicted. The
clock is injectable so TTL/LRU are testable without real time.
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


class SessionStore:
    def __init__(
        self,
        max_sessions: int | None = None,
        ttl_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _max = max_sessions if max_sessions is not None else _env_int(
            "GOLDENMATCH_MCP_SESSION_MAX", 64)
        self._max = max(1, _max)  # a 0/negative cap would make the store a no-op
        self._ttl = ttl_seconds if ttl_seconds is not None else _env_int(
            "GOLDENMATCH_MCP_SESSION_TTL", 3600)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()

    def put(self, session_id: str, session: Any) -> None:
        with self._lock:
            now = self._clock()
            self._entries[session_id] = (session, now)
            self._entries.move_to_end(session_id)
            self._evict(now)

    def get(self, session_id: str) -> Any | None:
        with self._lock:
            now = self._clock()
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            session, touched = entry
            if now - touched > self._ttl:
                del self._entries[session_id]
                return None
            self._entries.move_to_end(session_id)  # LRU touch; TTL clock is put()-anchored
            return session

    def _evict(self, now: float) -> None:
        # drop expired first
        for k in [k for k, (_, t) in self._entries.items() if now - t > self._ttl]:
            del self._entries[k]
        # then LRU until within cap
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


_STORE = SessionStore()
