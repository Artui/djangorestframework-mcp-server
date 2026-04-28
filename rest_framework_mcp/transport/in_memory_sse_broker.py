from __future__ import annotations

import asyncio
from typing import Any


class InMemorySSEBroker:
    """In-process per-session pub/sub for server-pushed MCP messages.

    Each subscribed session gets a private :class:`asyncio.Queue`. App code
    running in the same Python process publishes to it via :meth:`publish`;
    the streaming GET generator pulls off the queue and emits SSE frames.

    State is instance-scoped — the :class:`MCPServer` owns one broker, so
    multiple servers in the same process don't share state. Multi-process
    deployments need an out-of-process backend; see
    :class:`RedisSSEBroker` (in the ``[redis]`` extra) for the production
    choice.

    The broker enforces a single subscriber per session — if a client
    re-subscribes (e.g. after a dropped connection), the previous queue is
    replaced and the old generator will eventually error out on its next
    ``await``. There is no replay; clients that need durability should call
    ``tools/call`` directly rather than relying on SSE.
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
        had no subscriber. The caller decides how to react to a miss — most
        callers will ignore it (the client will catch up via a fresh
        ``tools/call`` round-trip).
        """
        queue: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if queue is None:
            return False
        await queue.put(payload)
        return True

    def has_subscriber(self, session_id: str) -> bool:
        return session_id in self._queues


__all__ = ["InMemorySSEBroker"]
