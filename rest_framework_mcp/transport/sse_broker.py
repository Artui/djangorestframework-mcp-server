from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SSEBroker(Protocol):
    """Pluggable pub/sub for server-pushed MCP messages.

    The transport calls :meth:`subscribe` when a client opens
    ``GET /mcp/``, :meth:`publish` from app code that wants to push a
    payload to a specific session, and :meth:`unsubscribe` when the
    streaming generator unwinds.

    Two concrete implementations ship today:

    - :class:`InMemorySSEBroker` — single-process, no infra. Suitable for
      development and single-worker ASGI deployments.
    - :class:`RedisSSEBroker` — Redis pub/sub. Required for multi-worker
      deployments where any worker can serve the streaming GET. Pulled in
      via the ``[redis]`` optional extra.

    The contract is intentionally narrow: a session has at most one live
    subscriber; ``publish`` returns ``True`` if a delivery was attempted,
    ``False`` if no subscriber was attached. Implementations decide whether
    ``publish`` is fire-and-forget or awaits delivery confirmation; the MCP
    transport treats it as best-effort either way.
    """

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, session_id: str, payload: Any) -> bool: ...

    def has_subscriber(self, session_id: str) -> bool: ...


__all__ = ["SSEBroker"]
