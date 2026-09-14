"""A ``401`` challenge is only honest where a token can change the answer.

Both transports answer ``GET /mcp/`` with ``405`` on configurations that offer
no server-pushed stream: the sync one always, the async one when no broker is
wired or sessions are off. Authentication used to run *first* on that path, so
an unauthenticated caller received ``401`` and an RFC 9728
``WWW-Authenticate`` challenge naming the protected-resource metadata document
— the signal an MCP client uses to begin an OAuth flow, which in desktop
clients opens a browser window. The flow could only ever end in ``405``.

The rule the package already applied elsewhere is the one that settles it:
``_handle_modern`` validates headers ahead of authentication because era
detection "reveals nothing about who is asking" and gating it on credentials
would break anonymous probes. Whether a resource supports a method is the same
kind of fact, and ``DELETE`` on the same endpoint has always answered it
without a credential.

Grouped in one file because the defect spans both transports and three
configurations, and because a per-transport home would hide that the two
siblings disagreed with each other.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.async_streamable_http_viewset import (
    ASYNC_STREAMABLE_HTTP_ACTION_MAP,
    AsyncStreamableHttpViewSet,
)
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.streamable_http_viewset import (
    STREAMABLE_HTTP_ACTION_MAP,
    StreamableHttpViewSet,
)

factory = RequestFactory()
_LEGACY_VERSION = "2025-11-25"
_MODERN_VERSION = "2026-07-28"


class _HeaderPrincipalBackend:
    """Authenticates on a header, so "no credential" is expressible.

    ``AllowAnyBackend`` authenticates every request as anonymous and so can
    never return ``401`` — which is why the default development configuration
    cannot reach any of this.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        principal: str | None = request.headers.get("X-Principal")
        if principal is None:
            return None
        return TokenInfo(user=SimpleNamespace(pk=principal))

    def protected_resource_metadata(self) -> dict:
        return {}

    def www_authenticate_challenge(self, *, scopes: Any = None, error: Any = None) -> str:
        del scopes, error
        return (
            'Bearer realm="mcp", '
            'resource_metadata="https://example.test/.well-known/oauth-protected-resource", '
            'error="invalid_token"'
        )


def _view(*, sessions: bool, is_async: bool = False, broker: Any = None) -> Any:
    common: dict[str, Any] = {
        "tools": ToolRegistry(),
        "resources": ResourceRegistry(),
        "prompts": PromptRegistry(),
        "auth_backend": _HeaderPrincipalBackend(),
        "session_store": InMemorySessionStore(),
        "config": build_mcp_config(sessions_enabled=sessions),
    }
    if is_async:
        return AsyncStreamableHttpViewSet.as_view(
            ASYNC_STREAMABLE_HTTP_ACTION_MAP, sse_broker=broker, **common
        )
    return StreamableHttpViewSet.as_view(STREAMABLE_HTTP_ACTION_MAP, **common)


def _get(*, version: str = _LEGACY_VERSION, principal: str | None = None) -> Any:
    headers: dict[str, str] = {"Mcp-Protocol-Version": version}
    if principal is not None:
        headers["X-Principal"] = principal
    return factory.get("/mcp/", headers=headers)


# ---------- the sync transport: no stream on any configuration ----------


@pytest.mark.parametrize("sessions", [True, False])
def test_sync_get_is_405_without_a_credential(sessions: bool) -> None:
    """WSGI implements no GET stream at all, so the setting cannot matter.

    The sessions check is absent here rather than merely late, which is what
    made every sync deployment with a real auth backend reach this, not only
    the sessionless ones.
    """
    response = _view(sessions=sessions)(_get())
    assert response.status_code == 405
    assert "WWW-Authenticate" not in response


@pytest.mark.parametrize("sessions", [True, False])
def test_sync_get_answers_the_same_with_a_credential(sessions: bool) -> None:
    """The point of the change: the credential never mattered here."""
    assert _view(sessions=sessions)(_get(principal="alice")).status_code == 405


def test_sync_get_is_405_for_a_modern_caller_too() -> None:
    """Previously ``401`` where the async sibling already answered ``405``."""
    assert _view(sessions=True)(_get(version=_MODERN_VERSION)).status_code == 405


# ---------- the async transport: 405 only where there is no stream ----------


async def test_async_get_is_405_without_a_credential_when_sessionless() -> None:
    """A session id *is* the channel address, so sessionless has none to open."""
    view = _view(sessions=False, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get())
    assert response.status_code == 405
    assert "WWW-Authenticate" not in response


async def test_async_get_is_405_without_a_credential_when_no_broker_is_wired() -> None:
    """Holds the ``broker is None`` conjunct of the guard on its own.

    ``MCPServer(sse_broker=None)`` with sessions *enabled* is a documented
    configuration (``docs/async.md``), and it reaches the identical defect —
    which is why this file does not scope the fix to ``SESSIONS_ENABLED``.
    """
    view = _view(sessions=True, is_async=True, broker=None)
    response = await view(_get())
    assert response.status_code == 405
    assert "WWW-Authenticate" not in response


async def test_async_get_is_405_without_a_credential_when_sessions_are_off() -> None:
    """Holds the ``not sessions_enabled`` conjunct on its own.

    Paired with the broker test above deliberately: the guard is one branch
    arc, so deleting either conjunct leaves line and branch coverage at 100%.
    These two are what fail instead.
    """
    view = _view(sessions=False, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get())
    assert response.status_code == 405


@pytest.mark.parametrize("sessions,broker", [(False, True), (True, False), (False, False)])
async def test_async_get_answers_the_same_with_a_credential(sessions: bool, broker: bool) -> None:
    """No credential moves any of these configurations off ``405``."""
    view = _view(sessions=sessions, is_async=True, broker=InMemorySSEBroker() if broker else None)
    assert (await view(_get(principal="alice"))).status_code == 405


# ---------- and 401 stays exactly where a token does decide the outcome ----------


async def test_async_get_still_challenges_when_a_stream_is_actually_on_offer() -> None:
    """The guard against over-correcting.

    Sessions on and a broker wired is the one GET configuration that serves a
    stream, so authentication genuinely decides the answer and the ``401`` —
    challenge and all — must survive.
    """
    view = _view(sessions=True, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get())
    assert response.status_code == 401
    assert "resource_metadata=" in response["WWW-Authenticate"]


async def test_async_get_past_the_gate_reaches_the_session_checks() -> None:
    """An authenticated caller on that same configuration is not short-circuited."""
    view = _view(sessions=True, is_async=True, broker=InMemorySSEBroker())
    response = await view(_get(principal="alice"))
    assert response.status_code == 404  # authenticated, but named no session


# ---------- DELETE already behaved, and is what proved the 401 bought nothing ----------


@pytest.mark.parametrize("is_async", [False, True])
async def test_delete_answers_405_without_a_credential(is_async: bool) -> None:
    """Unchanged: the same condition, already ahead of authentication."""
    view = _view(sessions=False, is_async=is_async)
    request = factory.delete("/mcp/")
    response = await view(request) if is_async else view(request)
    assert response.status_code == 405


@pytest.mark.parametrize("is_async", [False, True])
async def test_delete_discloses_the_setting_to_anyone_who_asks(is_async: bool) -> None:
    """Why hiding GET's ``405`` was never worth a spurious OAuth flow.

    ``DELETE`` distinguishes sessions-on from sessions-off without a
    credential, and sessions-off is precisely what makes the legacy ``GET``
    a ``405``. The confidentiality the old ordering claimed to protect was
    already published on the same endpoint by the neighbouring verb.
    """
    off = _view(sessions=False, is_async=is_async)
    on = _view(sessions=True, is_async=is_async)
    off_request, on_request = factory.delete("/mcp/"), factory.delete("/mcp/")
    off_response = await off(off_request) if is_async else off(off_request)
    on_response = await on(on_request) if is_async else on(on_request)
    assert off_response.status_code == 405
    assert on_response.status_code == 401
