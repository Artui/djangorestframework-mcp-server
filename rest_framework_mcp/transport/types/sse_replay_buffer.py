from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEReplayBuffer(Protocol):
    """Pluggable per-session ring buffer for SSE event replay.

    Pair this with an :class:`SSEBroker` to support
    [``Last-Event-ID``](https://html.spec.whatwg.org/multipage/server-sent-events.html#last-event-id)
    resume — when a client reconnects with that header, the SSE response
    generator drains every event past the supplied ID from the buffer
    *before* entering live mode, so the client sees no gap from the
    server's POV.

    The buffer is the single source of truth for event IDs:
    :meth:`record` assigns a new monotonic ID per session and returns it,
    so the live frame and any future replayed frame agree on the ID. The
    transport wraps that ID into the broker payload as
    ``{"_mcp_event_id", "_mcp_payload"}`` and the SSE response generator
    unwraps it to emit ``id:`` lines.

    Implementations should bound their per-session storage — replay
    buffers without a cap leak when clients never reconnect. The shipped
    in-memory variant uses a fixed-size :class:`collections.deque`; the
    Redis variant uses ``XADD MAXLEN ~ N`` for capped streams.

    Resume is **opt-in**: pass ``sse_replay_buffer=...`` to
    :class:`MCPServer` to enable it. When omitted, the SSE wire shape is
    unchanged (no ``id:`` lines) and ``Last-Event-ID`` from clients is
    silently ignored.
    """

    async def record(self, session_id: str, payload: Any) -> str:
        """Persist ``payload`` for ``session_id`` and return its event ID.

        The returned ID is what the SSE response emits as the ``id:`` line
        and what the client echoes back via ``Last-Event-ID`` on resume.
        IDs must be monotonic *within a session*; cross-session ordering
        is not required.
        """
        ...

    def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        """Yield ``(event_id, payload)`` pairs strictly after ``after_id``.

        ``after_id=None`` (no header sent) yields nothing — fresh subscribe
        is the no-replay path. An ``after_id`` that's older than the
        buffer's oldest retained event yields whatever is still in the
        ring (best-effort delivery; the client knows it lost some events
        only by counting). An ``after_id`` newer than the latest recorded
        event yields nothing — the client is already up to date.
        """
        ...

    async def forget(self, session_id: str) -> None:
        """Drop all retained events for ``session_id``.

        Called when a session is explicitly destroyed (DELETE) so dead
        sessions don't accumulate buffer state. Implementations that rely
        on TTL-based eviction can no-op this.
        """
        ...


__all__ = ["SSEReplayBuffer"]
