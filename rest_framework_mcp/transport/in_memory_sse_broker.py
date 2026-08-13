from __future__ import annotations

import asyncio
from typing import Any


class InMemorySSEBroker:
    """In-process per-session pub/sub for server-pushed MCP messages.

    Each subscribed session gets a private ``asyncio.Queue``. App code in
    the same process publishes to it via ``publish``; the streaming GET
    generator pulls off the queue and emits SSE frames.

    State is instance-scoped, so multiple servers in one process share none of
    it. Multi-process deployments need an out-of-process backend — see
    [`RedisSSEBroker`][rest_framework_mcp.transport.redis_sse_broker.RedisSSEBroker] (the ``[redis]`` extra).

    One subscriber per session: re-subscribing replaces the previous queue, and
    the old generator errors out on its next ``await``. There is no replay;
    clients needing durability call ``tools/call`` rather than relying on SSE.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Any]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
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
        had no subscriber. A miss is the caller's to react to, and most ignore
        it: the client catches up on a fresh ``tools/call`` round-trip.
        """
        queue: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if queue is None:
            return False
        await queue.put(payload)
        return True

    def has_subscriber(self, session_id: str) -> bool:
        return session_id in self._queues


__all__ = ["InMemorySSEBroker"]
