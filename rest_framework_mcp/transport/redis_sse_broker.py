from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from typing import Any


# ``redis`` is an optional extra, so importing this module without it must not
# crash the package; the ImportError fires only when a consumer constructs a
# ``RedisSSEBroker``. Resolved through ``importlib`` so the binding stays plain
# ``Any`` and the type checker cannot narrow it to the imported class.
def _resolve_async_redis() -> Any:
    try:
        return importlib.import_module("redis.asyncio").Redis
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


AsyncRedis: Any = _resolve_async_redis()


_DEFAULT_CHANNEL_PREFIX: str = "drf-mcp:sse"


class RedisSSEBroker:
    """Cross-process SSE broker backed by Redis pub/sub.

    Drop-in replacement for
    [`InMemorySSEBroker`][rest_framework_mcp.transport.in_memory_sse_broker.InMemorySSEBroker]
    when running multiple ASGI workers behind a load balancer. The streaming GET handler
    can land on any worker, and ``await server.notify(...)`` from a different worker
    still reaches the right session because every worker subscribes to the same Redis
    channel (``<prefix>:<session_id>``). JSON encode/decode happens at the broker
    boundary, so app code pushes Python dicts and the streaming generator sees dicts
    too.

    Wire it into [`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer]:

    ```python
    from redis.asyncio import Redis
    from rest_framework_mcp import MCPServer
    from rest_framework_mcp.transport.redis_sse_broker import RedisSSEBroker

    broker = RedisSSEBroker(Redis.from_url("redis://localhost:6379/0"))
    server = MCPServer(name="my-app", sse_broker=broker)
    ```

    Caveats:

    - Same single-subscriber-per-session contract as the in-memory broker:
      re-subscribing replaces the old subscriber's queue.
    - Replay is a separate, opt-in collaborator — pair this with
      [`RedisSSEReplayBuffer`][rest_framework_mcp.transport.redis_sse_replay_buffer.RedisSSEReplayBuffer]
      for cross-worker ``Last-Event-ID`` resume.
    - The Redis client's lifecycle is the consumer's: close it during ASGI
      lifespan shutdown.
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
        # cancels the previous task, so background coroutines cannot leak.
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, asyncio.Queue[Any]] = {}

    def _channel(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}"

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        # Re-subscribe replaces cleanly, mirroring the in-memory broker.
        existing: asyncio.Task[None] | None = self._tasks.pop(session_id, None)
        if existing is not None:
            existing.cancel()

        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._queues[session_id] = queue
        # The listener task owns the Redis pubsub object; the handle is what
        # lets unsubscribe cancel it.
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

        ``True`` when at least one listener was attached. ``False`` — zero
        subscribers — can also mean the streaming task has not connected yet,
        so a caller needing at-least-once delivery layers its own retry.
        """
        message: bytes = json.dumps(payload).encode()
        receivers: int = await self._client.publish(self._channel(session_id), message)
        return receivers > 0

    def has_subscriber(self, session_id: str) -> bool:
        """Local-only check: whether *this* worker has an active subscriber.

        Cross-process visibility would cost an extra Redis round-trip and buy
        nothing — the streaming generator only cares about its own queue.
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
                    # Skips ``subscribe`` ack frames and friends.
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
            # Best-effort: the Redis client may already be closed by the time
            # the listener is cancelled, especially during lifespan shutdown.
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.unsubscribe(self._channel(session_id))
            with contextlib.suppress(Exception):  # pragma: no cover
                await pubsub.aclose()


__all__ = ["RedisSSEBroker"]
