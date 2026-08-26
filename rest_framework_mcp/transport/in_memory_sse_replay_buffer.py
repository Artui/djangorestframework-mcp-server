from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from typing import Any


class InMemorySSEReplayBuffer:
    """In-process bounded replay buffer for SSE event resume.

    Each session holds its own ``collections.deque`` capped at
    ``max_events``, evicting the oldest event when a new one arrives. Event IDs
    are zero-padded monotonic integers per session, string-valued because the
    SSE wire format is string-only and clients echo them back verbatim via
    ``Last-Event-ID``.

    **Bounded in both directions.** ``max_events`` caps one session's history;
    ``max_sessions`` caps how many sessions are retained at all, dropping the
    least recently written when a new one arrives. The second bound is what
    stops the buffer growing for the life of the process: ``forget`` is called
    only when a client explicitly ``DELETE``s its session, while sessions
    ordinarily end by expiring in the session store — which notifies nothing —
    or by a client simply dropping the connection. Without the cap every
    session that ever recorded an event keeps its ring and its counter forever.

    An evicted session that is somehow still live restarts its numbering, and a
    reconnect carrying the old ``Last-Event-ID`` then replays nothing rather
    than replaying the wrong events. That is the same silent gap a client
    already accepts when its ring overflows, which is why the default is
    generous enough that reaching it means far more open sessions than one
    worker can serve streams for.

    **Single-process** ASGI deployments only: a resume can land on a different worker
    than the one that recorded the events, so multi-worker deployments need
    [`RedisSSEReplayBuffer`][rest_framework_mcp.transport.redis_sse_replay_buffer.RedisSSEReplayBuffer].
    State is instance-scoped, so multiple servers in one process share no replay
    history."""

    def __init__(self, *, max_events: int = 1024, max_sessions: int = 1024) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self._max_events: int = max_events
        self._max_sessions: int = max_sessions
        # Ordered by least-recently-written, which is what makes the oldest
        # entry the right one to drop.
        self._buffers: OrderedDict[str, deque[tuple[str, Any]]] = OrderedDict()
        # Kept separate from the ring so eviction cannot reset numbering: an
        # event keeps its ID for the client's lifetime even once evicted.
        # Dropped only with the whole session, so the two stay in step.
        self._counters: dict[str, int] = {}

    async def record(self, session_id: str, payload: Any) -> str:
        next_id: int = self._counters.get(session_id, 0) + 1
        self._counters[session_id] = next_id
        # Zero-padded so textual ordering matches numeric ordering, which the
        # ``>`` comparison in ``replay`` relies on.
        event_id: str = f"{next_id:016d}"
        ring: deque[tuple[str, Any]] | None = self._buffers.get(session_id)
        if ring is None:
            ring = deque(maxlen=self._max_events)
            self._buffers[session_id] = ring
        else:
            # Writing is what marks a session live, so it goes to the back of
            # the eviction queue.
            self._buffers.move_to_end(session_id)
        ring.append((event_id, payload))
        while len(self._buffers) > self._max_sessions:
            stale, _ = self._buffers.popitem(last=False)
            self._counters.pop(stale, None)
        return event_id

    async def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        if after_id is None:
            return
        ring: deque[tuple[str, Any]] | None = self._buffers.get(session_id)
        if ring is None:
            return
        # ``after_id`` is the last ID the client *did* see, so only strictly
        # greater IDs are yielded.
        for event_id, payload in list(ring):
            if event_id > after_id:
                yield event_id, payload

    async def forget(self, session_id: str) -> None:
        self._buffers.pop(session_id, None)
        self._counters.pop(session_id, None)


__all__ = ["InMemorySSEReplayBuffer"]
