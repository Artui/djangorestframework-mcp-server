from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from typing import Any


def _resolve_async_redis() -> Any:
    """Resolve ``redis.asyncio.Redis`` without making it an import-time dependency.

    ``redis`` is an optional extra, so importing this module must work without
    it — the ``ImportError`` fires only when a consumer actually constructs a
    broker. Resolved through ``importlib`` so the binding stays plain ``Any``
    and the type checker does not narrow it to the imported class.
    """
    try:
        return importlib.import_module("redis.asyncio").Redis
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


AsyncRedis: Any = _resolve_async_redis()

_DEFAULT_CHANNEL_PREFIX: str = "drf-mcp:sub"


class RedisSubscriptionBroker:
    """Cross-process topic fan-out over Redis pub/sub. **The deployable one.**

    Subscriptions are the feature where a single-process broker fails most
    quietly: the write that triggers a notification lands on whichever worker
    served that request, and the subscriber's stream is parked on another, so
    :class:`InMemorySubscriptionBroker` delivers to nobody and the client simply
    never hears that anything changed. Anything past one worker wants this::

        from redis.asyncio import Redis
        from rest_framework_mcp.subscriptions.redis_subscription_broker import (
            RedisSubscriptionBroker,
        )

        server = MCPServer(
            name="my-app",
            subscription_broker=RedisSubscriptionBroker(Redis.from_url("redis://…")),
        )

    ⚠ **One Redis channel per topic, one listener task per subscription.** The
    session broker's shape — a channel per session — does not transfer: a
    subscription watches several topics and several subscriptions watch the same
    topic, so the mapping is many-to-many in both directions. Each subscription
    gets one task subscribed to all of its channels at once, feeding the single
    queue its stream reads. That keeps the task count proportional to *live
    subscriptions* rather than to topics, which is the number that is bounded by
    connections.

    The Redis client's lifecycle belongs to the consumer — close it during ASGI
    lifespan shutdown, as with the other Redis collaborators here.
    """

    def __init__(self, client: Any, *, channel_prefix: str = _DEFAULT_CHANNEL_PREFIX) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSubscriptionBroker requires the `redis` package. "
                'Install with `pip install "djangorestframework-mcp-server[redis]"`.'
            )
        self._client: Any = client
        self._prefix: str = channel_prefix
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def _channel(self, topic: str) -> str:
        return f"{self._prefix}:{topic}"

    def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        if topics:
            self._tasks[id(queue)] = asyncio.create_task(self._listen(topics, queue))
        # A subscription that named nothing gets a queue and no listener rather
        # than an error: an empty filter is a legal (if odd) request, and the
        # acknowledgement is where the client learns it will hear nothing.
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        task: asyncio.Task[None] | None = self._tasks.pop(id(queue), None)
        if task is not None:
            task.cancel()

    async def publish(self, topic: str, payload: Any) -> int:
        """Publish to ``topic``'s channel; returns Redis's subscriber count.

        ⚠ The count is **cluster-wide receivers, not confirmed deliveries** —
        and a listener that has not finished connecting yet reports as zero. It
        is a diagnostic, not a delivery guarantee; notifications are best-effort
        by design and a client that missed one re-reads the resource.
        """
        message: bytes = json.dumps(payload).encode()
        receivers: int = await self._client.publish(self._channel(topic), message)
        return int(receivers)

    async def _listen(self, topics: frozenset[str], queue: asyncio.Queue[Any]) -> None:
        """Pump every subscribed channel into this subscription's single queue."""
        channels: list[str] = [self._channel(topic) for topic in sorted(topics)]
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(*channels)
            async for message in pubsub.listen():  # pragma: no branch - exits via cancel
                if message.get("type") != "message":
                    # Subscribe acknowledgements and friends.
                    continue
                data: Any = message.get("data")
                if isinstance(data, bytes | bytearray):  # pragma: no branch - always bytes
                    data = data.decode()
                await queue.put(json.loads(data))
        except asyncio.CancelledError:
            raise
        finally:
            # Best-effort: during ASGI lifespan shutdown the client may already
            # be closed by the time this task is cancelled, and either call
            # raising here would be noise.
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.unsubscribe(*channels)
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.aclose()


__all__ = ["RedisSubscriptionBroker"]
