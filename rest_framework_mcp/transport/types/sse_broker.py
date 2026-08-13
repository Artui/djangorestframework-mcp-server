from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEBroker(Protocol):
    """Pluggable pub/sub for server-pushed MCP messages.

    The transport calls ``subscribe`` when a client opens ``GET /mcp/``,
    ``publish`` from app code pushing a payload to a specific session, and
    ``unsubscribe`` when the streaming generator unwinds.

    Two implementations ship: [`InMemorySSEBroker`][rest_framework_mcp.transport.in_memory_sse_broker.InMemorySSEBroker] (single-process, no
    infra) and [`RedisSSEBroker`][rest_framework_mcp.transport.redis_sse_broker.RedisSSEBroker] (the ``[redis]`` extra), required for
    multi-worker deployments where any worker can serve the streaming GET.

    The contract is deliberately narrow: a session has at most one live
    subscriber, and ``publish`` returns ``True`` if a delivery was attempted,
    ``False`` if no subscriber was attached. Whether ``publish`` is
    fire-and-forget or awaits confirmation is the implementation's choice; the
    transport treats it as best-effort either way.
    """

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, session_id: str, payload: Any) -> bool: ...

    def has_subscriber(self, session_id: str) -> bool: ...


__all__ = ["SSEBroker"]
