"""Progress over an SSE response stream: the streaming half of the transport.

A client asks by putting a ``progressToken`` in the request's ``_meta``; the
server answers with ``text/event-stream`` carrying ``notifications/progress``
frames and then the response. Everything here drives the real async URL conf,
because the subject is what comes back over HTTP — content type, frame order,
which status was committed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import AsyncClient, override_settings
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, ScopeRequired
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _meta(token: Any = "abc123", *, modern: bool = True) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if token is not None:
        meta["progressToken"] = token
    if modern:
        meta["io.modelcontextprotocol/protocolVersion"] = MODERN
        meta["io.modelcontextprotocol/clientCapabilities"] = {}
    return meta


def _server(**config: Any) -> MCPServer:
    server = MCPServer(
        name="p",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(**config) if config else None,
    )

    def export(*, progress: Any) -> dict[str, Any]:
        progress(1, total=3, message="starting")
        progress(2, total=3, meta={"com.example/stage": "middle"})
        progress(3, total=3, message="done")
        return {"rows": 3}

    server.register_service_tool(
        name="p.export",
        description="Export things.",
        spec=ServiceSpec(service=export, atomic=False),
    )
    server.register_selector_tool(
        name="p.gated",
        description="Gated.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: None),
        permissions=[ScopeRequired(["mcp:admin"])],
    )
    return server


async def _post(
    server: MCPServer,
    *,
    name: str = "p.export",
    token: Any = "abc123",
    modern: bool = True,
) -> Any:
    params: dict[str, Any] = {"name": name, "arguments": {}, "_meta": _meta(token, modern=modern)}
    headers: dict[str, str] = {"Mcp-Protocol-Version": MODERN if modern else LEGACY}
    if modern:
        headers |= {"Mcp-Method": "tools/call", "Mcp-Name": name}
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        return await AsyncClient().post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}),
            content_type="application/json",
            headers=headers,
        )


async def _frames(response: Any) -> list[dict[str, Any]]:
    """Parse the ``data:`` lines of an SSE body into JSON-RPC messages."""
    body = b"".join([chunk async for chunk in response]).decode()
    return [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]


# ----- the happy path -----


@pytest.mark.django_db(transaction=True)
async def test_a_progress_token_turns_the_response_into_a_stream() -> None:
    response = await _post(_server())
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    # Without this nginx buffers the whole body and flushes on close, which
    # defeats the point of streaming at all.
    assert response["X-Accel-Buffering"] == "no"


@pytest.mark.django_db(transaction=True)
async def test_progress_frames_arrive_before_the_response() -> None:
    frames = await _frames(await _post(_server()))
    assert [f.get("method") for f in frames] == [
        "notifications/progress",
        "notifications/progress",
        "notifications/progress",
        None,
    ]
    assert frames[0]["params"] == {
        "progressToken": "abc123",
        "progress": 1,
        "total": 3,
        "message": "starting",
    }
    # The final frame is the ordinary JSON-RPC response, and it terminates the
    # stream.
    assert frames[-1]["id"] == 1
    assert frames[-1]["result"]["structuredContent"] == {"rows": 3}


@pytest.mark.django_db(transaction=True)
async def test_structured_detail_rides_in_the_notification_meta() -> None:
    """Where the protocol puts extension data, so `message` stays prose."""
    frames = await _frames(await _post(_server()))
    assert frames[1]["params"]["_meta"] == {"com.example/stage": "middle"}
    assert "message" not in frames[1]["params"]


@pytest.mark.django_db(transaction=True)
async def test_progress_streams_for_a_legacy_client_too() -> None:
    """``_meta.progressToken`` sits in the same place in both eras."""
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        client = AsyncClient()
        init = await client.post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}),
            content_type="application/json",
        )
        session = init.headers["Mcp-Session-Id"]
        response = await client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "p.export",
                        "arguments": {},
                        "_meta": {"progressToken": 7},
                    },
                }
            ),
            content_type="application/json",
            headers={"Mcp-Protocol-Version": LEGACY, "Mcp-Session-Id": session},
        )
        assert response["Content-Type"] == "text/event-stream"
        frames = await _frames(response)
    assert frames[0]["params"]["progressToken"] == 7


# ----- when not to stream -----


@pytest.mark.django_db(transaction=True)
async def test_no_token_means_no_stream() -> None:
    """A stream whose only event is the final response buys nothing."""
    response = await _post(_server(), token=None)
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["result"]["structuredContent"] == {"rows": 3}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("token", [True, 1.5, {"a": 1}, None])
async def test_a_token_that_is_not_a_string_or_int_is_treated_as_absent(token: Any) -> None:
    """Declining to send progress is always legal; failing the call is not."""
    response = await _post(_server(), token=token)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")


# ----- the status streaming would otherwise cost -----


@pytest.mark.django_db(transaction=True)
async def test_a_denied_tool_still_gets_403_and_a_challenge() -> None:
    """The reason permissions are pre-flighted.

    A streaming response commits its status before the handler runs, so a
    denial found inside would ride as an in-stream error inside a ``200`` —
    losing the ``403`` the authorization spec makes normative and the
    challenge telling the client what to ask for.
    """
    response = await _post(_server(), name="p.gated")
    assert response.status_code == 403
    assert response["Content-Type"].startswith("application/json")
    assert "WWW-Authenticate" in response.headers
    assert "mcp:admin" in response["WWW-Authenticate"]


# ----- bounds -----


@pytest.mark.django_db(transaction=True)
async def test_notifications_are_capped() -> None:
    """The spec asks both parties to rate-limit; a per-row reporter floods."""
    server = MCPServer(
        name="p",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(max_progress_notifications=2),
    )

    def chatty(*, progress: Any) -> dict[str, Any]:
        for i in range(1, 50):
            progress(i, total=50)
        return {"ok": True}

    server.register_service_tool(
        name="p.export", description="x", spec=ServiceSpec(service=chatty, atomic=False)
    )
    frames = await _frames(await _post(server))
    assert sum(1 for f in frames if f.get("method") == "notifications/progress") == 2
    # Capping the *reports* must not cap the work: the result still arrives.
    assert frames[-1]["result"]["structuredContent"] == {"ok": True}


@pytest.mark.django_db(transaction=True)
async def test_a_non_increasing_report_is_dropped() -> None:
    """The spec makes increase a MUST, so forwarding one would violate it here."""
    server = MCPServer(
        name="p", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )

    def backwards(*, progress: Any) -> dict[str, Any]:
        progress(5, total=10)
        progress(3, total=10)  # dropped
        progress(5, total=10)  # dropped — equal is not an increase
        progress(9, total=10)
        return {"ok": True}

    server.register_service_tool(
        name="p.export", description="x", spec=ServiceSpec(service=backwards, atomic=False)
    )
    frames = await _frames(await _post(server))
    reported = [f["params"]["progress"] for f in frames if f.get("method")]
    assert reported == [5, 9]


# ----- failure inside a committed stream -----


@pytest.mark.django_db(transaction=True)
async def test_an_exception_becomes_an_in_stream_error_not_a_truncated_body() -> None:
    """There is no status left to change once the stream is open."""
    server = MCPServer(
        name="p", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )

    def boom(*, progress: Any) -> dict[str, Any]:
        progress(1, total=2)
        raise RuntimeError("nope")

    server.register_service_tool(
        name="p.export", description="x", spec=ServiceSpec(service=boom, atomic=False)
    )
    frames = await _frames(await _post(server))
    assert frames[0]["method"] == "notifications/progress"
    assert frames[-1]["error"]["code"] == -32603
    assert "nope" in frames[-1]["error"]["message"]


# ----- the stream's own mechanics -----


async def _drain(response: Any) -> bytes:
    return b"".join([chunk async for chunk in response])


async def test_a_quiet_dispatch_still_emits_keepalives() -> None:
    """Something has to hold the connection open past an idle proxy timeout."""
    import asyncio

    from rest_framework_mcp.transport.response_stream import build_response_stream

    async def slow(reporter: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        reporter(1, total=1)
        return {"done": True}

    response = build_response_stream(
        dispatch=slow, request_id=1, progress_token="t", max_notifications=10, keepalive=0.01
    )
    body = (await _drain(response)).decode()
    assert ": keepalive" in body
    assert '"done": true' in body.replace(" ", "").replace('"done":true', '"done": true')


async def test_a_report_with_no_total_omits_the_field() -> None:
    """`total` is omitted rather than guessed — a wrong one renders a wrong bar."""
    from rest_framework_mcp.transport.response_stream import build_response_stream

    async def one(reporter: Any) -> dict[str, Any]:
        reporter(1)
        return {}

    response = build_response_stream(
        dispatch=one, request_id=1, progress_token="t", max_notifications=10, keepalive=5
    )
    frames = await _frames(response)
    assert frames[0]["params"] == {"progressToken": "t", "progress": 1}


async def test_closing_the_stream_cancels_the_dispatch() -> None:
    """Cancellation-by-disconnect: the client going away *is* the signal.

    ⚠ It cancels the await, not the work — a thread parked in a driver's
    socket read is not interruptible by asyncio, the same caveat the dispatch
    deadline carries.
    """
    import asyncio

    from rest_framework_mcp.transport.response_stream import _stream

    seen: list[str] = []

    async def never(reporter: Any) -> dict[str, Any]:
        reporter(1, total=100)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        return {}  # pragma: no cover - the sleep never completes

    # Driving the generator rather than the response: Django wraps the
    # iterator, and closing the wrapper does not reach through to the
    # generator's ``finally`` — which is the whole subject here.
    iterator = _stream(
        dispatch=never, request_id=1, progress_token="t", max_notifications=10, keepalive=5
    )
    assert b"progress" in await iterator.__anext__()
    # The client hangs up: Django closes the generator, whose ``finally``
    # cancels the dispatch.
    await iterator.aclose()
    await asyncio.sleep(0.05)
    assert seen == ["cancelled"]


async def test_a_report_after_the_client_left_is_dropped_not_raised() -> None:
    """A reporter must not raise into domain code that cannot defend itself."""
    import asyncio

    from rest_framework_mcp.transport.response_stream import _StreamReporter

    closed = asyncio.new_event_loop()
    closed.close()
    reporter = _StreamReporter(loop=closed, queue=asyncio.Queue(), token="t", remaining=5)
    assert reporter(1, total=2) is None


# ----- the permission pre-flight only speaks for tool calls -----


def test_preflight_stands_aside_for_everything_it_cannot_speak_for() -> None:
    """An unknown tool, or a bad name, is the handler's error to report — with
    a better message than a pre-flight could give."""
    from django.http import HttpRequest

    from rest_framework_mcp.auth.types.token_info import TokenInfo
    from rest_framework_mcp.handlers.types.context import MCPCallContext
    from rest_framework_mcp.transport.progress_dispatch import preflight_permissions

    server = _server()
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=MODERN,
        config=server.config,
    )
    assert preflight_permissions("resources/read", {"uri": "x://y"}, context) is None
    assert preflight_permissions("tools/call", "not-a-dict", context) is None
    assert preflight_permissions("tools/call", {"name": 7}, context) is None
    assert preflight_permissions("tools/call", {"name": "nope"}, context) is None
