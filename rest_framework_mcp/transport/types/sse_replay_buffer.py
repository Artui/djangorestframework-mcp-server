from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEReplayBuffer(Protocol):
    """Pluggable per-session ring buffer for SSE event replay.

    Pair this with an :class:`SSEBroker` to support
    [``Last-Event-ID``](https://html.spec.whatwg.org/multipage/server-sent-events.html#last-event-id)
    resume: when a client reconnects with that header, the SSE response
    generator drains every event past the supplied ID *before* entering live
    mode, so the client sees no gap.

    The buffer is the single source of truth for event IDs: :meth:`record`
    assigns a new monotonic ID per session, so the live frame and any replayed
    frame agree. The transport wraps that ID into the broker payload as
    ``{"_mcp_event_id", "_mcp_payload"}`` and the response generator unwraps it
    to emit ``id:`` lines.

    Implementations must bound their per-session storage — an uncapped buffer
    leaks when clients never reconnect.

    Resume is **opt-in**: pass ``sse_replay_buffer=...`` to :class:`MCPServer`.
    When omitted there are no ``id:`` lines and ``Last-Event-ID`` is ignored.
    """

    async def record(self, session_id: str, payload: Any) -> str:
        """Persist ``payload`` for ``session_id`` and return its event ID.

        The ID is what the response emits as the ``id:`` line and what the
        client echoes back via ``Last-Event-ID``. IDs must be monotonic
        *within a session*; cross-session ordering is not required.
        """
        ...

    def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        """Yield ``(event_id, payload)`` pairs strictly after ``after_id``.

        ``after_id=None`` yields nothing — a fresh subscribe is the no-replay
        path. An ``after_id`` older than the oldest retained event yields
        whatever is still held, best-effort: the client can tell it lost events
        only by counting. One newer than the latest recorded event yields
        nothing, the client being up to date.
        """
        ...

    async def forget(self, session_id: str) -> None:
        """Drop all retained events for ``session_id``.

        Called when a session is explicitly destroyed, so dead sessions do not
        accumulate buffer state. Implementations relying on TTL eviction can
        no-op this.
        """
        ...


__all__ = ["SSEReplayBuffer"]
