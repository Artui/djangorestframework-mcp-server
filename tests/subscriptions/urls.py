"""A mounted server that can push notifications, for the end-to-end suite.

Module-level so the broker outlives a single request the way it does in a real
deployment — a subscription's whole point is that the event it carries was
produced by a *different* request.
"""

from __future__ import annotations

from typing import Any

from django.urls import path
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

BROKER = InMemorySubscriptionBroker()


class _Denies:
    def has_permission(self, request: Any, token: Any) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["r:read"]


def _selector() -> list[dict[str, str]]:
    return [{"ok": "1"}]


def _build() -> MCPServer:
    server = MCPServer(
        name="subs-e2e",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        subscription_broker=BROKER,
    )
    server.register_resource(
        name="open",
        uri_template="open://thing",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
    )
    server.register_resource(
        name="gated",
        uri_template="gated://thing",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
        permissions=[_Denies()],
    )
    return server


SERVER = _build()

urlpatterns: list[Any] = [path("mcp/", SERVER.async_urls)]
