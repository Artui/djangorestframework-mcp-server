from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from django.http import StreamingHttpResponse

from rest_framework_mcp.transport.types.sse_broker import SSEBroker
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer

# Time between idle keep-alive comments. The MCP spec prescribes no value;
# 15 s avoids most intermediate proxy timeouts (60 s+ idle is risky behind
# nginx / cloudflare) without flooding the connection.
_KEEPALIVE_INTERVAL_SECONDS: float = 15.0


def keepalive_interval_seconds() -> float:
    """The idle keep-alive period shared by both SSE surfaces.

    Shared so the POST response stream and the GET session stream cannot drift
    apart on a value whose whole purpose is matching what the proxies in front
    of them tolerate.
    """
    return _KEEPALIVE_INTERVAL_SECONDS


def format_event(payload: Any, *, event_id: str | None = None) -> bytes:
    """Encode a single JSON-RPC payload as one SSE event.

    SSE events are delimited by a blank line. ``event_id``, when set, becomes
    an ``id:`` line preceding the ``data:`` payload; clients echo the latest
    seen ID back via ``Last-Event-ID`` on reconnect.
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
    max_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    """Async generator that yields SSE bytes for one session's stream.

    Subscribes to ``broker`` for ``session_id``, emits an opening comment so
    intermediaries know the stream is alive, then drains the queue forever.
    Idle periods produce ``: keepalive`` comment frames so proxies do not close
    the connection, and a client disconnect unsubscribes the session cleanly
    through the ``finally``.

    With a ``replay_buffer``, every event recorded after ``last_event_id`` is replayed
    on open as ``id: <id>\\ndata: <payload>\\n\\n`` frames before live mode begins, and
    live frames arrive wrapped as ``{"_mcp_event_id", "_mcp_payload"}`` (see
    [`MCPServer.notify`][rest_framework_mcp.server.mcp_server.MCPServer.notify]),
    unpacked here so the wire stays SSE-shaped. Without one, no ``id:`` lines are
    emitted and ``last_event_id`` is ignored.

    ``max_seconds`` closes the stream from this end once it elapses, with a
    comment frame rather than an abrupt cut so an operator reading the wire can
    tell a lifetime cap from a crash. Checked at the top of each wait, so the
    close lands within one keep-alive period of the deadline. The client
    reconnects on its own — that is what SSE clients do — and with a replay
    buffer the reconnect is gapless."""
    queue: asyncio.Queue[Any] = broker.subscribe(session_id)
    deadline: float | None = (
        None if max_seconds is None else asyncio.get_running_loop().time() + max_seconds
    )
    try:
        yield b": stream open\n\n"
        if replay_buffer is not None and last_event_id is not None:
            async for event_id, payload in replay_buffer.replay(  # pragma: no branch
                session_id, last_event_id
            ):
                yield format_event(payload, event_id=event_id)
        while True:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                # The authentication check happened once, when the stream
                # opened. Ending it forces a reconnect, which re-runs that
                # check, so a revoked principal stops receiving a session's
                # pushes within one window rather than indefinitely.
                yield b": stream closed\n\n"
                return
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
                yield format_event(payload["_mcp_payload"], event_id=payload["_mcp_event_id"])
            else:
                yield format_event(payload)
    finally:
        broker.unsubscribe(session_id, queue)


def build_sse_response(
    broker: SSEBroker,
    session_id: str,
    *,
    keepalive_interval: float = _KEEPALIVE_INTERVAL_SECONDS,
    replay_buffer: SSEReplayBuffer | None = None,
    last_event_id: str | None = None,
    max_seconds: float | None = None,
) -> StreamingHttpResponse:
    """Build the spec-compliant ``StreamingHttpResponse`` for an SSE GET.

    ``X-Accel-Buffering: no`` disables nginx response buffering; without it
    nginx waits for the connection to close before flushing, defeating SSE.
    """
    response = StreamingHttpResponse(
        stream_events(
            broker,
            session_id,
            keepalive_interval=keepalive_interval,
            replay_buffer=replay_buffer,
            last_event_id=last_event_id,
            max_seconds=max_seconds,
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


__all__ = ["build_sse_response", "format_event", "keepalive_interval_seconds", "stream_events"]
