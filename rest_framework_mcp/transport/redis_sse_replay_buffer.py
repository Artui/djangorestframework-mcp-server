from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from typing import Any


def _resolve_async_redis() -> Any:
    """Load ``redis.asyncio.Redis`` when the optional extra is present."""
    try:
        return importlib.import_module("redis.asyncio").Redis
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


AsyncRedis: Any = _resolve_async_redis()


_DEFAULT_KEY_PREFIX: str = "drf-mcp:sse-replay"


class RedisSSEReplayBuffer:
    """Cross-process replay buffer backed by Redis Streams.

    Drop-in replacement for
    [`InMemorySSEReplayBuffer`][rest_framework_mcp.transport.in_memory_sse_replay_buffer.InMemorySSEReplayBuffer]
    when running multiple ASGI workers: a reconnect can land on any worker, and a shared
    Redis Stream replays the same events whichever worker recorded them.

    Stream IDs are auto-assigned by Redis and monotonic within a session, so
    they double as the SSE event IDs the client echoes back via
    ``Last-Event-ID``. ``MAXLEN ~ N`` caps the retained history per session,
    approximately — Redis trims when convenient, which is fine here.

    Wire it into [`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer]::

        from redis.asyncio import Redis
        from rest_framework_mcp import MCPServer
        from rest_framework_mcp.transport.redis_sse_replay_buffer import (
            RedisSSEReplayBuffer,
        )

        client = Redis.from_url("redis://localhost:6379/0")
        buffer = RedisSSEReplayBuffer(client, max_events=2048)
        server = MCPServer(name="my-app", sse_broker=..., sse_replay_buffer=buffer)

    The Redis client is the consumer's responsibility — close it during
    ASGI lifespan shutdown.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_events: int = 1024,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
    ) -> None:
        if AsyncRedis is None:  # pragma: no cover - exercised by the no-extras smoke job
            raise ImportError(
                "RedisSSEReplayBuffer requires the `redis` package. "
                'Install with `pip install "djangorestframework-mcp-server[redis]"`.'
            )
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._client: Any = client
        self._max_events: int = max_events
        self._prefix: str = key_prefix

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}"

    async def record(self, session_id: str, payload: Any) -> str:
        """Append ``payload`` to the session's stream and return the assigned ID.

        ``XADD <key> MAXLEN ~ N * data <json>``: the ``*`` lets Redis choose a
        monotonic ID, and ``~`` trims at internal node boundaries, which bounds
        memory in the same shape as exact trimming and is faster.
        """
        body: str = json.dumps(payload)
        # Decoded from bytes so the ID survives JSON round-trips and SSE
        # framing.
        raw_id: Any = await self._client.xadd(
            self._key(session_id),
            {"data": body},
            maxlen=self._max_events,
            approximate=True,
        )
        if isinstance(raw_id, bytes | bytearray):
            return raw_id.decode()
        return str(raw_id)  # pragma: no cover - real & fake redis both return bytes

    async def replay(self, session_id: str, after_id: str | None) -> AsyncIterator[tuple[str, Any]]:
        if after_id is None:
            return
        # The exclusive lower bound (``(<id>``) yields every entry strictly
        # greater than ``after_id``.
        entries: Any = await self._client.xrange(self._key(session_id), min=f"({after_id}")
        for raw_id, fields in entries:
            event_id: str = (
                raw_id.decode() if isinstance(raw_id, bytes | bytearray) else str(raw_id)
            )
            data: Any = fields.get(b"data") or fields.get("data")
            if isinstance(  # pragma: no branch - real & fake redis both yield bytes
                data, bytes | bytearray
            ):
                data = data.decode()
            yield event_id, json.loads(data)

    async def forget(self, session_id: str) -> None:
        await self._client.delete(self._key(session_id))


__all__ = ["RedisSSEReplayBuffer"]
