from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEBroker(Protocol):
    """Pluggable pub/sub for server-pushed MCP messages.

    The transport calls ``subscribe`` when a client opens ``GET /mcp/``,
    ``publish`` from app code pushing a payload to a specific session, and
    ``unsubscribe`` when the streaming generator unwinds.

    Two implementations ship:
    [`InMemorySSEBroker`][rest_framework_mcp.transport.in_memory_sse_broker.InMemorySSEBroker]
    (single-process, no infra) and
    [`RedisSSEBroker`][rest_framework_mcp.transport.redis_sse_broker.RedisSSEBroker]
    (the ``[redis]`` extra), required for multi-worker deployments where any worker can
    serve the streaming GET.

    The contract is deliberately narrow: a session has at most one live
    subscriber, and ``publish`` returns ``True`` if a delivery was attempted,
    ``False`` if no subscriber was attached. Whether ``publish`` is
    fire-and-forget or awaits confirmation is the implementation's choice; the
    transport treats it as best-effort either way.

    Implementations must also bound what an undrained subscriber can accumulate.
    A stream the client stops reading is not an error, and there is no
    backpressure channel to the publisher, so an unbounded queue turns one
    paused consumer into unbounded resident memory.
    """

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, session_id: str, payload: Any) -> bool: ...

    def has_subscriber(self, session_id: str) -> bool: ...

    @property
    def active_streams(self) -> int:
        """How many session streams this worker is currently serving.

        What ``MAX_CONCURRENT_SSE_STREAMS`` is measured against, so it is a
        per-worker count of local subscribers rather than a cluster-wide one:
        the resource being protected is this process's task pool.
        """
        ...


__all__ = ["SSEBroker"]
