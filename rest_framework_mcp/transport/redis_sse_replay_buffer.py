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

    Drop-in replacement for :class:`InMemorySSEReplayBuffer` when running
    multiple ASGI workers. The streaming GET that handles a reconnect
    can land on any worker; reading from a shared Redis Stream means the
    replay is the same regardless of which worker recorded the events.

    Stream IDs are auto-assigned by Redis (``ms-seq`` format) and are
    monotonic within a session — they double as the SSE event IDs the
    client echoes back via ``Last-Event-ID``. ``MAXLEN ~ N`` caps the
    retained history per session; the ``~`` makes trimming approximate
    (Redis trims when convenient) which is fine for replay buffers.

    Wire it into :class:`MCPServer`::

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

        ``XADD <key> MAXLEN ~ N * data <json>`` — the ``*`` lets Redis
        choose a monotonic ID; ``~`` makes trimming approximate (Redis
        trims at internal node boundaries, which is faster than exact
        trimming and bounds memory in the same shape).
        """
        body: str = json.dumps(payload)
        # ``redis-py`` returns the assigned ID as bytes; decode to a plain
        # string so it survives JSON round-trips and SSE wire framing.
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
        # ``XRANGE`` with an exclusive lower bound (``(<id>``) yields every
        # entry strictly greater than ``after_id``. Redis returns
        # ``[(id, {b"data": b"<json>"}), ...]`` so we unpack and decode.
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
