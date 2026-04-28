from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from django.http import StreamingHttpResponse

from rest_framework_mcp.transport.sse_broker import SSEBroker
from rest_framework_mcp.transport.sse_replay_buffer import SSEReplayBuffer

# Time between idle keep-alive comments. The MCP spec doesn't prescribe a
# value; 15 s is a common compromise that avoids most intermediate proxy
# timeouts (60 s+ idle is risky behind nginx / cloudflare) without flooding
# the connection.
_KEEPALIVE_INTERVAL_SECONDS: float = 15.0


def _format_event(payload: Any, *, event_id: str | None = None) -> bytes:
    """Encode a single JSON-RPC payload as one SSE event.

    SSE events are delimited by a blank line. ``event_id``, when set,
    becomes an ``id:`` line preceding the ``data:`` payload — clients
    echo the latest seen ID back via ``Last-Event-ID`` on reconnect.
    """
    body: str = json.dumps(payload, separators=(",", ":"))
    if event_id is None:
        return f"data: {body}\n\n".encode()
    return f"id: {event_id}\ndata: {body}\n\n".encode()


async def stream_events(
    broker: SSEBroker,
    session_id: str,
    *,
    keepalive_interval: float = _KEEPALIVE_INTERVAL_SECONDS,
    replay_buffer: SSEReplayBuffer | None = None,
    last_event_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Async generator that yields SSE bytes for one session's stream.

    Subscribes to ``broker`` for ``session_id``, emits an opening comment so
    intermediaries know the stream is alive, then drains the queue forever.
    Idle periods produce ``: keepalive`` comment frames so proxies don't
    close the connection. Cancellation (client disconnect) unsubscribes the
    session cleanly via the ``finally`` block.

    When ``replay_buffer`` is supplied:

    - On open, every event recorded after ``last_event_id`` is replayed
      first as ``id: <id>\\ndata: <payload>\\n\\n`` frames so the client
      catches up before live mode begins.
    - Live frames arrive on the broker queue wrapped as
      ``{"_mcp_event_id", "_mcp_payload"}`` (see :meth:`MCPServer.notify`);
      the wrapper is unpacked here so the wire stays SSE-shaped.

    Without a buffer no ``id:`` lines are emitted and ``last_event_id`` is
    ignored — backward-compatible with the v1 wire.
    """
    queue: asyncio.Queue[Any] = broker.subscribe(session_id)
    try:
        yield b": stream open\n\n"
        if replay_buffer is not None and last_event_id is not None:
            async for event_id, payload in replay_buffer.replay(  # pragma: no branch
                session_id, last_event_id
            ):
                yield _format_event(payload, event_id=event_id)
        while True:
            try:
                payload: Any = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            if (
                replay_buffer is not None
                and isinstance(payload, dict)
                and "_mcp_event_id" in payload
                and "_mcp_payload" in payload
            ):
                yield _format_event(payload["_mcp_payload"], event_id=payload["_mcp_event_id"])
            else:
                yield _format_event(payload)
    finally:
        broker.unsubscribe(session_id, queue)


def build_sse_response(
    broker: SSEBroker,
    session_id: str,
    *,
    keepalive_interval: float = _KEEPALIVE_INTERVAL_SECONDS,
    replay_buffer: SSEReplayBuffer | None = None,
    last_event_id: str | None = None,
) -> StreamingHttpResponse:
    """Build the spec-compliant ``StreamingHttpResponse`` for an SSE GET.

    ``X-Accel-Buffering: no`` disables nginx response buffering; without it
    nginx waits for the connection to close before flushing, defeating SSE.
    Other reverse proxies follow similar conventions.
    """
    response = StreamingHttpResponse(
        stream_events(
            broker,
            session_id,
            keepalive_interval=keepalive_interval,
            replay_buffer=replay_buffer,
            last_event_id=last_event_id,
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


__all__ = ["build_sse_response", "stream_events"]
