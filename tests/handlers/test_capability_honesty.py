"""A capability is a promise, and two of them can only be kept for one era.

Every push flag — the three ``listChanged`` fields and ``subscribe`` — describes
a notification that leaves through ``subscriptions/listen``, which is a
modern-only method. ``extensions`` is not even a field on the legacy
``ServerCapabilities``. Advertising either to a legacy client promises something
nothing on its path can deliver, and a client acting on it does not get a clean
error — for ``listChanged`` it gets silence, which is worse.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.handlers.handle_initialize import build_capabilities, handle_initialize
from rest_framework_mcp.handlers.handle_server_discover import handle_server_discover
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.subscriptions.test_subscription_core import _context, _server
from tests.tasks.conftest import RecordingExecutor, slow_service

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _pushing_server() -> MCPServer:
    """A server with everything behind every flag — so any suppression below is
    the era's doing and not a missing collaborator."""
    return _server(subscription_broker=InMemorySubscriptionBroker())


def _full_server() -> MCPServer:
    store = InMemoryTaskStore()
    server = _server(
        subscription_broker=InMemorySubscriptionBroker(),
        task_store=store,
        task_executor=RecordingExecutor(store),
    )
    server.register_service_tool(
        name="x", description="x", spec=ServiceSpec(service=slow_service, atomic=False)
    )
    return server


def _full_context(server: MCPServer, **overrides: Any) -> Any:
    """``_context`` does not thread the executor, and the tasks capability needs
    both halves — a store without one creates tasks nothing will ever run."""
    return _context(server, task_executor=server.task_executor, **overrides)


# ---------- the bundle, per era ----------


def test_a_modern_caller_is_told_about_the_pushes() -> None:
    caps = build_capabilities(_context(_pushing_server()), modern=True)
    assert caps.resources == {"listChanged": True, "subscribe": True}
    assert caps.prompts is None or caps.prompts == {"listChanged": True}


def test_a_legacy_caller_is_not() -> None:
    """⚠ The bug this closes. Same server, same broker — the difference is only
    who is asking, and a legacy client has no method to receive any of it."""
    caps = build_capabilities(_context(_pushing_server()), modern=False)
    assert caps.resources == {}


def test_the_registries_themselves_are_unaffected() -> None:
    """Suppressing the flags must not suppress the capability. A legacy client
    still has resources to read — ``{}`` means supported."""
    caps = build_capabilities(_context(_pushing_server()), modern=False)
    assert caps.resources == {}
    assert caps.resources is not None


def test_a_legacy_caller_is_not_offered_extensions() -> None:
    """Not a field on the legacy ``ServerCapabilities`` at all — extension
    negotiation arrived with ``2026-07-28`` — and the tasks path is unreachable
    for a legacy client anyway, since the extension must be declared per
    request."""
    assert build_capabilities(_full_context(_full_server()), modern=False).extensions is None


def test_a_modern_caller_is() -> None:
    assert build_capabilities(_full_context(_full_server()), modern=True).extensions is not None


# ---------- how each handler decides ----------


def test_initialize_never_advertises_a_push_whatever_the_context_says() -> None:
    """⚠ ``initialize`` does not exist in ``2026-07-28``, so reaching the handler
    *is* the proof of era. Pinned with a context claiming a modern version, which
    is exactly the state that would make a version-sniffing implementation get it
    wrong."""
    result = handle_initialize(
        {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "t"}},
        _full_context(_full_server(), protocol_version=MODERN),
    )
    assert result.capabilities.resources == {}
    assert result.capabilities.extensions is None


def test_discover_follows_the_caller_not_the_server() -> None:
    """The versions and the identity are properties of the endpoint; these two
    capabilities are properties of the caller's reach."""
    server = _full_server()
    modern = handle_server_discover(None, _full_context(server, protocol_version=MODERN))
    legacy = handle_server_discover(None, _full_context(server, protocol_version=LEGACY))
    assert modern["capabilities"]["resources"] == {"listChanged": True, "subscribe": True}
    assert legacy["capabilities"]["resources"] == {}
    assert "extensions" not in legacy["capabilities"]


# ---------- what a legacy client would have done with the promise ----------


@pytest.mark.django_db(transaction=True)
@pytest.mark.urls("tests.subscriptions.urls")
def test_the_method_a_legacy_client_would_have_called_does_not_exist(client: Client) -> None:
    """⛔ ``resources/subscribe`` is deliberately not implemented: optional in
    ``2025-11-25``, and in ``2026-07-28`` the schema says
    ``SubscriptionFilter.resourceUris`` *"replaces the former resources/subscribe
    RPC"*. This is the concrete thing the old advertisement invited a client to
    do — so the fix is the advertisement, not the method."""
    handshake = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LEGACY,
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            }
        ),
        content_type="application/json",
    )
    capabilities: dict[str, Any] = json.loads(handshake.content)["result"]["capabilities"]
    assert "subscribe" not in capabilities.get("resources", {})

    attempt = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/subscribe",
                "params": {"uri": "open://thing"},
            }
        ),
        content_type="application/json",
        headers={
            "Mcp-Session-Id": handshake["Mcp-Session-Id"],
            "Mcp-Protocol-Version": LEGACY,
        },
    )
    assert json.loads(attempt.content)["error"]["code"] == -32601
