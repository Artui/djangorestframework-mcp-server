from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
from typing import Any


def _resolve_async_redis() -> Any:
    """Resolve ``redis.asyncio.Redis`` without making it an import-time dependency.

    ``redis`` is an optional extra, so importing this module must work without
    it — the ``ImportError`` fires only when a consumer constructs a broker.
    Resolved through ``importlib`` so the binding stays plain ``Any`` and the
    type checker does not narrow it to the imported class.
    """
    try:
        return importlib.import_module("redis.asyncio").Redis
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


AsyncRedis: Any = _resolve_async_redis()

_DEFAULT_CHANNEL_PREFIX: str = "drf-mcp:sub"
_NAMESPACE_DIGEST_CHARS: int = 12


class RedisSubscriptionBroker:
    """Cross-process topic fan-out over Redis pub/sub. **The deployable one.**

    [`InMemorySubscriptionBroker`][rest_framework_mcp.subscriptions.in_memory_subscription_broker.InMemorySubscriptionBroker]
    delivers to nobody once the publisher and the subscriber's stream land on
    different workers, and does so
    silently, so anything past one worker wants this:

        from redis.asyncio import Redis
        from rest_framework_mcp.subscriptions.redis_subscription_broker import (
            RedisSubscriptionBroker,
        )

        server = MCPServer(
            name="my-app",
            subscription_broker=RedisSubscriptionBroker(
                Redis.from_url("redis://…"), namespace="my-app"
            ),
        )

    **Pass ``namespace`` whenever one Redis serves more than one server.**
    Topic names are built from caller-supplied values — a notification kind, a
    resource URI — so two servers that register the same ``SelectorSpec`` under
    the same URI derive the same topic. Sharing a broker (or two brokers on the
    same default prefix) then routes one server's change signals to the other's
    subscribers, past the ``resources/read`` permission ``grant_subscription``
    gates them on. The cache-backed session and task stores fold the server's
    ``name`` into their key prefix for exactly this reason; a Redis client is
    the consumer's to construct, so here it is a constructor argument. The value
    is hashed into the prefix, since ``name`` is free-form.

    **One Redis channel per topic, one listener task per subscription.** The
    mapping is many-to-many — a subscription watches several topics and several
    subscriptions watch one topic — so each subscription gets a single task
    subscribed to all of its channels, feeding the one queue its stream reads.
    That keeps the task count proportional to live subscriptions rather than to
    topics, and it is subscriptions that connections bound.

    The Redis client's lifecycle belongs to the consumer — close it during ASGI
    lifespan shutdown, as with the other Redis collaborators here.
    """

    def __init__(
        self,
        client: Any,
        *,
        channel_prefix: str = _DEFAULT_CHANNEL_PREFIX,
        namespace: str | None = None,
    ) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSubscriptionBroker requires the `redis` package. "
                'Install with `pip install "djangorestframework-mcp-server[redis]"`.'
            )
        self._client: Any = client
        self._prefix: str = (
            channel_prefix if namespace is None else f"{channel_prefix}:{_digest(namespace)}"
        )
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._pubsubs: dict[int, tuple[Any, list[str]]] = {}

    def _channel(self, topic: str) -> str:
        return f"{self._prefix}:{topic}"

    async def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]:
        """Register the channels **before returning**, then pump them.

        **The await is the whole point.** Registering inside the background task
        would return a queue that is not yet subscribed, and the caller emits
        "you are subscribed" immediately afterwards, so everything published in
        that window goes nowhere while the client has been told otherwise — a
        race this class's own deployment makes likely, since the publisher is
        another process and waits for nobody.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue()
        if not topics:
            # An empty filter is legal: no channels, no pubsub, no task, and the
            # acknowledgement is where the client learns it will hear nothing.
            return queue
        channels: list[str] = [self._channel(topic) for topic in sorted(topics)]
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*channels)
        self._pubsubs[id(queue)] = (pubsub, channels)
        self._tasks[id(queue)] = asyncio.create_task(self._pump(pubsub, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        task: asyncio.Task[None] | None = self._tasks.pop(id(queue), None)
        if task is not None:
            task.cancel()
        entry = self._pubsubs.pop(id(queue), None)
        if entry is not None:
            pubsub, channels = entry
            # Scheduled rather than awaited: ``unsubscribe`` is called from a
            # generator's ``finally``, which may run during interpreter or loop
            # shutdown where awaiting is not available. Its failure mode — a
            # channel released late — is bounded by the connection closing.
            asyncio.ensure_future(self._release(pubsub, channels))  # noqa: RUF006

    @property
    def active_subscriptions(self) -> int:
        return len(self._tasks)

    async def _release(self, pubsub: Any, channels: list[str]) -> None:
        """Give the channels back. Best-effort by construction.

        During ASGI lifespan shutdown the client may already be closed by the
        time a subscription unwinds, and neither call failing is worth raising
        into a finished request.
        """
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(*channels)
        with contextlib.suppress(Exception):
            await pubsub.aclose()

    async def publish(self, topic: str, payload: Any) -> int:
        """Publish to ``topic``'s channel; returns Redis's subscriber count.

        The count is **cluster-wide receivers, not confirmed deliveries**, and a
        listener still connecting reports as zero. A diagnostic, not a delivery
        guarantee: notifications are best-effort and a client that missed one
        re-reads the resource.
        """
        message: bytes = json.dumps(payload).encode()
        receivers: int = await self._client.publish(self._channel(topic), message)
        return int(receivers)

    async def _pump(self, pubsub: Any, queue: asyncio.Queue[Any]) -> None:
        """Feed already-subscribed channels into this subscription's one queue."""
        async for message in pubsub.listen():  # pragma: no branch - exits via cancel
            if message.get("type") != "message":
                continue
            data: Any = message.get("data")
            if isinstance(data, bytes | bytearray):  # pragma: no branch - always bytes
                data = data.decode()
            await queue.put(json.loads(data))


def _digest(namespace: str) -> str:
    """Hash a free-form server name into something a channel name can hold."""
    return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:_NAMESPACE_DIGEST_CHARS]


__all__ = ["RedisSubscriptionBroker"]
