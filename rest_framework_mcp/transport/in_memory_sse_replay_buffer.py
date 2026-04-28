from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from typing import Any


class InMemorySSEReplayBuffer:
    """In-process bounded replay buffer for SSE event resume.

    Each session holds its own :class:`collections.deque` capped at
    ``max_events``; the oldest event is evicted when a new one arrives.
    Event IDs are zero-padded monotonic integers per session — string-
    valued because the SSE wire format is string-only and clients echo
    them back verbatim via ``Last-Event-ID``.

    Suitable for **single-process** ASGI deployments. Multi-worker
    deployments must use :class:`RedisSSEReplayBuffer` because the
    streaming GET that handles a resume can land on a different worker
    than the one that recorded the events.

    State is instance-scoped — :class:`MCPServer` owns one buffer, so
    multiple servers in the same process don't share replay history.
    """

    def __init__(self, *, max_events: int = 1024) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._max_events: int = max_events
        # Per-session ring of (event_id, payload). The deque's ``maxlen``
        # gives us O(1) bounded retention without manual trimming.
        self._buffers: dict[str, deque[tuple[str, Any]]] = {}
        # Per-session monotonic counter. Kept separate from the deque so
        # eviction doesn't reset numbering — a recorded event keeps its ID
        # for the client's lifetime even after eviction from the ring.
        self._counters: dict[str, int] = {}

    async def record(self, session_id: str, payload: Any) -> str:
        next_id: int = self._counters.get(session_id, 0) + 1
        self._counters[session_id] = next_id
        # Zero-pad to keep textual ordering match numeric ordering up to
        # 10^16 events per session (effectively forever for SSE use).
        event_id: str = f"{next_id:016d}"
        ring: deque[tuple[str, Any]] = self._buffers.setdefault(
            session_id, deque(maxlen=self._max_events)
        )
        ring.append((event_id, payload))
        return event_id

    async def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        if after_id is None:
            return
        ring: deque[tuple[str, Any]] | None = self._buffers.get(session_id)
        if ring is None:
            return
        # Linear scan — replay buffers are bounded so this is O(N) per
        # reconnect, not per event. ``after_id`` is the last ID the client
        # *did* see, so we yield strictly greater IDs.
        for event_id, payload in list(ring):
            if event_id > after_id:
                yield event_id, payload

    async def forget(self, session_id: str) -> None:
        self._buffers.pop(session_id, None)
        self._counters.pop(session_id, None)


__all__ = ["InMemorySSEReplayBuffer"]
