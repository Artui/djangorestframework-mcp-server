from __future__ import annotations

import asyncio
import json

from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.in_memory_sse_replay_buffer import InMemorySSEReplayBuffer
from rest_framework_mcp.transport.sse_response import (
    _format_event,
    build_sse_response,
    stream_events,
)


def test_format_event_emits_data_line_with_blank_separator() -> None:
    out = _format_event({"a": 1})
    assert out.endswith(b"\n\n")
    assert b"data: " in out
    payload = out[len(b"data: ") : -2].decode()
    assert json.loads(payload) == {"a": 1}


async def test_stream_events_yields_open_then_payload() -> None:
    broker = InMemorySSEBroker()

    async def producer() -> None:
        # Wait long enough for the consumer to subscribe before publishing.
        await asyncio.sleep(0.01)
        await broker.publish("s", {"hello": "world"})

    asyncio.create_task(producer())

    gen = stream_events(broker, "s", keepalive_interval=1.0)
    opening = await gen.__anext__()
    assert opening == b": stream open\n\n"

    payload_frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert payload_frame.startswith(b"data: ")
    assert json.loads(payload_frame[len(b"data: ") : -2].decode()) == {"hello": "world"}

    await gen.aclose()
    assert not broker.has_subscriber("s")


async def test_stream_events_emits_keepalive_on_idle() -> None:
    broker = InMemorySSEBroker()
    gen = stream_events(broker, "s", keepalive_interval=0.05)
    await gen.__anext__()  # opening comment
    first = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
    # Drive a second loop iteration so the ``continue`` after the keep-alive
    # actually executes (the generator otherwise stays parked at ``yield``).
    second = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
    assert first == b": keepalive\n\n"
    assert second == b": keepalive\n\n"
    await gen.aclose()


def test_build_sse_response_sets_required_headers() -> None:
    broker = InMemorySSEBroker()
    response = build_sse_response(broker, "s")
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["Cache-Control"] == "no-cache"
    assert response["X-Accel-Buffering"] == "no"


# ---------- replay-buffer-aware behaviour ----------


def test_format_event_emits_id_line_when_event_id_set() -> None:
    out = _format_event({"a": 1}, event_id="abc")
    assert out.startswith(b"id: abc\ndata: ")
    assert out.endswith(b"\n\n")


async def test_stream_replays_buffered_events_before_live() -> None:
    """A reconnect with ``last_event_id`` drains the buffer first, then live."""
    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()
    # Pre-record three events. Client claims to have seen the first.
    first = await buffer.record("s", {"n": 1})
    second = await buffer.record("s", {"n": 2})
    third = await buffer.record("s", {"n": 3})

    gen = stream_events(
        broker,
        "s",
        keepalive_interval=5.0,
        replay_buffer=buffer,
        last_event_id=first,
    )
    assert (await gen.__anext__()) == b": stream open\n\n"
    # Replay yields events strictly after ``first``: event 2 then event 3,
    # each carrying the ``id:`` prefix.
    frame_2 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    frame_3 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert frame_2 == f"id: {second}\ndata: ".encode() + b'{"n":2}\n\n'
    assert frame_3 == f"id: {third}\ndata: ".encode() + b'{"n":3}\n\n'
    await gen.aclose()


async def test_live_events_unwrap_event_id_when_buffer_present() -> None:
    """When a buffer is wired in, broker payloads are wrapped — unwrap on the wire."""
    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()

    async def producer() -> None:
        await asyncio.sleep(0.01)
        # Simulate what ``MCPServer.notify`` publishes when a buffer is wired:
        # a wrapped envelope with the event ID inside.
        await broker.publish(
            "s",
            {"_mcp_event_id": "0000000000000007", "_mcp_payload": {"hi": "world"}},
        )

    asyncio.create_task(producer())

    gen = stream_events(
        broker, "s", keepalive_interval=1.0, replay_buffer=buffer, last_event_id=None
    )
    await gen.__anext__()  # opening
    frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert frame == b'id: 0000000000000007\ndata: {"hi":"world"}\n\n'
    await gen.aclose()


async def test_live_events_passthrough_when_no_event_id_wrapper() -> None:
    """A raw payload (legacy producer) still works under a buffer-enabled stream."""
    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()

    async def producer() -> None:
        await asyncio.sleep(0.01)
        # Plain payload — no wrapper. The generator falls back to the
        # buffer-less wire shape (no ``id:`` line).
        await broker.publish("s", {"plain": True})

    asyncio.create_task(producer())

    gen = stream_events(
        broker, "s", keepalive_interval=1.0, replay_buffer=buffer, last_event_id=None
    )
    await gen.__anext__()
    frame = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert frame == b'data: {"plain":true}\n\n'
    await gen.aclose()


async def test_replay_yields_nothing_falls_through_to_live() -> None:
    """``Last-Event-ID`` past the latest recorded event → replay is empty, live runs."""
    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()
    head = await buffer.record("s", {"n": 1})  # client claims to have seen everything

    gen = stream_events(
        broker,
        "s",
        keepalive_interval=0.05,
        replay_buffer=buffer,
        last_event_id=head,
    )
    assert (await gen.__anext__()) == b": stream open\n\n"
    # No replay (client up to date) → next frame is a keep-alive (idle live mode).
    frame = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
    assert frame == b": keepalive\n\n"
    await gen.aclose()


async def test_no_replay_when_last_event_id_omitted() -> None:
    """Header absent → no replay even when a buffer is configured."""
    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()
    await buffer.record("s", {"n": 1})  # buffered but client didn't ask to resume

    gen = stream_events(
        broker, "s", keepalive_interval=0.05, replay_buffer=buffer, last_event_id=None
    )
    assert (await gen.__anext__()) == b": stream open\n\n"
    # Next frame must be a keep-alive — no replay, no live events queued.
    frame = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
    assert frame == b": keepalive\n\n"
    await gen.aclose()
