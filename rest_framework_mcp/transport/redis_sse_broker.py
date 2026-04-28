from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from typing import Any


# ``redis`` is an optional extra. Importing this module without ``redis``
# installed must not crash the package; the ImportError only fires when a
# consumer actually constructs a ``RedisSSEBroker``. Resolved via
# ``importlib`` so the binding is plain ``Any`` (or ``None``) and the type
# checker doesn't narrow it to the imported class.
def _resolve_async_redis() -> Any:
    try:
        return importlib.import_module("redis.asyncio").Redis
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


AsyncRedis: Any = _resolve_async_redis()


_DEFAULT_CHANNEL_PREFIX: str = "drf-mcp:sse"


class RedisSSEBroker:
    """Cross-process SSE broker backed by Redis pub/sub.

    Drop-in replacement for :class:`InMemorySSEBroker` when running
    multiple ASGI workers behind a load balancer. The streaming GET handler
    can land on any worker; ``await server.notify(...)`` from a different
    worker reaches the right session because every worker subscribes to the
    same Redis channel.

    Each session subscribes to its own Redis channel (``<prefix>:<session_id>``)
    and runs a background ``asyncio.Task`` that pulls messages off the
    Redis pub/sub stream and pushes them onto a local
    :class:`asyncio.Queue` — the same queue shape the SSE response generator
    expects. JSON encode/decode happens at the broker boundary so app code
    pushes Python dicts and the streaming generator sees them as dicts too.

    Wire it into :class:`MCPServer`:

    .. code-block:: python

        from redis.asyncio import Redis
        from rest_framework_mcp import MCPServer
        from rest_framework_mcp.transport.redis_sse_broker import RedisSSEBroker

        broker = RedisSSEBroker(Redis.from_url("redis://localhost:6379/0"))
        server = MCPServer(name="my-app", sse_broker=broker)

    Caveats:

    - Same single-subscriber-per-session contract as the in-memory broker
      (re-subscribing replaces the old subscriber's queue).
    - No message replay; ``Last-Event-ID`` resume is a separate feature
      tracked in Phase 7c.
    - The Redis client's lifecycle is the consumer's responsibility — close
      it during ASGI lifespan shutdown.
    """

    def __init__(self, client: Any, *, channel_prefix: str = _DEFAULT_CHANNEL_PREFIX) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSSEBroker requires the `redis` package. "
                'Install with `pip install "djangorestframework-mcp-server[redis]"`.'
            )
        self._client: Any = client
        self._prefix: str = channel_prefix
        # Per-session listener tasks plus the queues they feed. Re-subscribe
        # cancels the previous task so we don't leak background coroutines.
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, asyncio.Queue[Any]] = {}

    def _channel(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}"

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        # Cancel any prior listener for this session — re-subscribe replaces
        # cleanly, mirroring the in-memory broker's contract.
        existing: asyncio.Task[None] | None = self._tasks.pop(session_id, None)
        if existing is not None:
            existing.cancel()

        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._queues[session_id] = queue
        # The listener task owns the Redis pubsub object; we keep a handle to
        # it so unsubscribe can cancel cleanly.
        self._tasks[session_id] = asyncio.create_task(self._listen(session_id, queue))
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        current: asyncio.Queue[Any] | None = self._queues.get(session_id)
        if current is not queue:
            return  # stale unsubscribe from a replaced subscriber — no-op.
        self._queues.pop(session_id, None)
        task: asyncio.Task[None] | None = self._tasks.pop(session_id, None)
        if task is not None:  # pragma: no branch - subscribe always pairs queue+task
            task.cancel()

    async def publish(self, session_id: str, payload: Any) -> bool:
        """Publish to the session's channel and report whether anyone received it.

        ``redis.publish`` returns the number of subscribers that got the
        message; we surface ``True`` when at least one listener was attached
        (typical case), ``False`` otherwise. Note that "0 subscribers" can
        also mean the streaming task hasn't connected yet — callers that
        require strict at-least-once delivery should layer their own retry.
        """
        message: bytes = json.dumps(payload).encode()
        receivers: int = await self._client.publish(self._channel(session_id), message)
        return receivers > 0

    def has_subscriber(self, session_id: str) -> bool:
        """Local-only check.

        Reflects whether *this* worker has an active subscriber. Across-
        process visibility would require an extra Redis round-trip and isn't
        useful for the typical caller (the streaming generator only cares
        about its own queue).
        """
        return session_id in self._queues

    async def _listen(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        """Pump messages from Redis pub/sub into the per-session queue.

        Cancellation propagates through ``pubsub.unsubscribe()`` /
        ``pubsub.aclose()`` so the Redis side cleans up on shutdown.
        """
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(self._channel(session_id))
            async for message in pubsub.listen():  # pragma: no branch - loop exits via cancel
                if message.get("type") != "message":
                    # ``subscribe`` ack frames and friends are ignored.
                    continue
                data: Any = message.get("data")
                if isinstance(
                    data, bytes | bytearray
                ):  # pragma: no branch - fakeredis always bytes
                    data = data.decode()
                await queue.put(json.loads(data))
        except asyncio.CancelledError:
            raise
        finally:
            # Cleanup is best-effort: the Redis client may already be closed
            # by the time the listener task is cancelled (especially during
            # ASGI lifespan shutdown). Either call raising is harmless.
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.unsubscribe(self._channel(session_id))
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.aclose()


__all__ = ["RedisSSEBroker"]
