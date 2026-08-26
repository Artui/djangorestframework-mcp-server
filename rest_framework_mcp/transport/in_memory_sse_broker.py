from __future__ import annotations

import asyncio
from typing import Any


class InMemorySSEBroker:
    """In-process per-session pub/sub for server-pushed MCP messages.

    Each subscribed session gets a private ``asyncio.Queue``. App code in
    the same process publishes to it via ``publish``; the streaming GET
    generator pulls off the queue and emits SSE frames.

    State is instance-scoped, so multiple servers in one process share none of it.
    Multi-process deployments need an out-of-process backend — see
    [`RedisSSEBroker`][rest_framework_mcp.transport.redis_sse_broker.RedisSSEBroker]
    (the ``[redis]`` extra).

    One subscriber per session: re-subscribing replaces the previous queue, and
    the old generator errors out on its next ``await``. There is no replay;
    clients needing durability call ``tools/call`` rather than relying on SSE.

    **Each queue is bounded, and a full one drops its oldest payload.** A
    client that opens the stream and stops reading it drains nothing, while
    ``notify`` keeps enqueueing — a paused consumer would otherwise pin one
    payload of memory per notification for as long as it holds the connection.
    Dropping rather than blocking is the only option that keeps the publisher
    honest: ``publish`` is called from request handling, so waiting on a reader
    that may never return would park the writer too. Dropping is also already
    the contract — delivery is best-effort, and a client that missed a
    notification re-reads — and the drop is *reported*, as the ``False`` that
    ``publish`` already uses for "nobody got this".
    """

    def __init__(self, *, max_queued_events: int = 1024) -> None:
        if max_queued_events <= 0:
            raise ValueError("max_queued_events must be positive")
        self._max_queued_events: int = max_queued_events
        self._queues: dict[str, asyncio.Queue[Any]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_queued_events)
        self._queues[session_id] = queue
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        """Remove ``queue`` from the registry if it's still the live subscriber.

        Compares by identity so a re-subscribed session doesn't accidentally
        unregister the new queue when the old generator shuts down.
        """
        current: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if current is queue:
            self._queues.pop(session_id, None)

    async def publish(self, session_id: str, payload: Any) -> bool:
        """Enqueue ``payload`` for ``session_id`` if a subscriber exists.

        Returns ``True`` if delivery was attempted, ``False`` if the session
        had no subscriber **or if the queue was full and the oldest payload was
        dropped to make room**. A miss is the caller's to react to, and most
        ignore it: the client catches up on a fresh ``tools/call`` round-trip.
        """
        queue: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if queue is None:
            return False
        if queue.full():
            # Evict the oldest rather than await room that a stalled reader
            # will never make. ``get_nowait`` cannot raise here: the queue is
            # full, and nothing else consumes it between these two lines.
            queue.get_nowait()
            queue.put_nowait(payload)
            return False
        queue.put_nowait(payload)
        return True

    def has_subscriber(self, session_id: str) -> bool:
        return session_id in self._queues

    @property
    def active_streams(self) -> int:
        return len(self._queues)


__all__ = ["InMemorySSEBroker"]
