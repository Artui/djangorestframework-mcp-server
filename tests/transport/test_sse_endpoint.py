from __future__ import annotations

import asyncio
import json

import pytest
from django.test import AsyncClient, RequestFactory, override_settings

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.async_streamable_http_view import AsyncStreamableHttpView
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker


@pytest.fixture
def async_urlconf():
    with override_settings(ROOT_URLCONF="tests.testapp.async_urls"):
        yield


async def _initialize(client: AsyncClient) -> str:
    response = await client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "x", "version": "0"},
                },
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    return response["Mcp-Session-Id"]


async def test_get_with_valid_session_returns_stream(async_urlconf) -> None:
    """A live session opens an SSE stream with the right content type."""
    client = AsyncClient()
    sid = await _initialize(client)
    response = await client.get(
        "/mcp/",
        headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["Cache-Control"] == "no-cache"
    # Drain a single chunk so the streaming generator unwinds cleanly.
    streaming = response.streaming_content
    first = await streaming.__anext__()
    assert first == b": stream open\n\n"
    await streaming.aclose()


async def test_published_payload_reaches_open_stream() -> None:
    """A payload pushed via the broker arrives as a ``data:`` SSE event.

    Driving the view directly with ``RequestFactory`` lets us own the broker
    instance and synchronise publish vs read without racing the test.
    """
    broker = InMemorySSEBroker()
    store = InMemorySessionStore()
    sid = store.create()

    factory = RequestFactory()
    request = factory.get(
        "/mcp/", headers={"Mcp-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid}
    )
    view = AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=AllowAnyBackend(),
        session_store=store,
        sse_broker=broker,
    )
    response = await view(request)
    assert response.status_code == 200

    streaming = response.streaming_content
    opening = await streaming.__anext__()
    assert opening == b": stream open\n\n"

    # Push from "another part of the app" — the running stream should receive it.
    await broker.publish(sid, {"event": "did-thing"})
    payload_frame = await asyncio.wait_for(streaming.__anext__(), timeout=1.0)
    assert payload_frame.startswith(b"data: ")
    assert json.loads(payload_frame[len(b"data: ") : -2].decode()) == {"event": "did-thing"}

    await streaming.aclose()
    # Generator cleanup (unsubscribe) is exercised directly in
    # ``test_sse_response.py``; Django's StreamingHttpResponse wrapper doesn't
    # always propagate ``aclose`` to the underlying coroutine in deterministic
    # time, so we don't assert on the broker's subscriber set here.


async def test_resume_replays_buffered_events_then_live() -> None:
    """A reconnect with ``Last-Event-ID`` drains buffered events first, then live.

    Drives the view via ``RequestFactory`` so the broker / buffer / session
    store are all owned by the test — there's no race between recording the
    pre-reconnect events and the GET handler reading them.
    """
    from rest_framework_mcp.transport.in_memory_sse_replay_buffer import (
        InMemorySSEReplayBuffer,
    )

    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()
    store = InMemorySessionStore()
    sid = store.create()

    # Pre-record three events; the client claims to have seen the first.
    first = await buffer.record(sid, {"n": 1})
    second = await buffer.record(sid, {"n": 2})
    third = await buffer.record(sid, {"n": 3})

    factory = RequestFactory()
    request = factory.get(
        "/mcp/",
        headers={
            "Mcp-Protocol-Version": "2025-11-25",
            "Mcp-Session-Id": sid,
            "Last-Event-Id": first,
        },
    )
    view = AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=AllowAnyBackend(),
        session_store=store,
        sse_broker=broker,
        sse_replay_buffer=buffer,
    )
    response = await view(request)
    assert response.status_code == 200

    streaming = response.streaming_content
    assert (await streaming.__anext__()) == b": stream open\n\n"
    frame_2 = await asyncio.wait_for(streaming.__anext__(), timeout=1.0)
    frame_3 = await asyncio.wait_for(streaming.__anext__(), timeout=1.0)
    assert frame_2 == f"id: {second}\ndata: ".encode() + b'{"n":2}\n\n'
    assert frame_3 == f"id: {third}\ndata: ".encode() + b'{"n":3}\n\n'
    await streaming.aclose()


async def test_delete_purges_replay_buffer() -> None:
    """DELETE on a session forgets buffered events so dead sessions don't accumulate."""
    from rest_framework_mcp.transport.in_memory_sse_replay_buffer import (
        InMemorySSEReplayBuffer,
    )

    broker = InMemorySSEBroker()
    buffer = InMemorySSEReplayBuffer()
    store = InMemorySessionStore()
    sid = store.create()
    await buffer.record(sid, {"n": 1})

    factory = RequestFactory()
    request = factory.delete("/mcp/", headers={"Mcp-Session-Id": sid})
    view = AsyncStreamableHttpView.as_view(
        tools=ToolRegistry(),
        resources=ResourceRegistry(),
        auth_backend=AllowAnyBackend(),
        session_store=store,
        sse_broker=broker,
        sse_replay_buffer=buffer,
    )
    response = await view(request)
    assert response.status_code == 204

    # Buffer was forgotten — replay yields nothing now.
    out: list[tuple[str, object]] = []
    async for pair in buffer.replay(sid, "0000000000000000"):
        out.append(pair)
    assert out == []


async def test_get_blocked_origin_returns_403(async_urlconf, settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["https://allowed.example"],
        "AUTH_BACKEND": "rest_framework_mcp.auth.backends.allow_any_backend.AllowAnyBackend",
        "SESSION_STORE": "rest_framework_mcp.transport.in_memory_session_store.InMemorySessionStore",
        "SERVER_INFO": {},
    }
    client = AsyncClient()
    response = await client.get("/mcp/", headers={"Origin": "https://blocked.example"})
    assert response.status_code == 403
