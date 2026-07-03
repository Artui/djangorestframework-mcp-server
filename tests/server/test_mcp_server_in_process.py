"""``MCPServer.list_tools`` / ``acall_tool`` — the public in-process transport.

The full-transport in-process surface (distinct from the spec-core
:meth:`MCPServer.call_tool`): tool listing with the merged ``inputSchema`` +
per-caller listing filter + pagination, and tool execution with the
transport-level MCP permissions / rate limits, the selector post-fetch pipeline
(filter / order / paginate), and chain tools — none of which the spec core
applies. These tests prove the methods delegate to the wire handlers (the
behaviour itself is covered exhaustively under ``tests/handlers/``) and build the
call context correctly off-HTTP.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, JsonRpcError, JsonRpcErrorCode, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.permissions.django_perm_required import DjangoPermRequired
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


class _Deny:
    def has_permission(self, request: Any, token: Any) -> bool:  # noqa: ARG002
        return False

    def required_scopes(self) -> list[str]:
        return ["x"]


class _DenyAllSpecPerm(BasePermission):
    """A DRF spec-level ``permission_classes`` entry that denies at class level."""

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return False


def _list_invoices() -> Any:
    return Invoice.objects.all()


# ----- list_tools -----


def test_list_tools_returns_the_merged_input_schema() -> None:
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        ordering_fields=["amount_cents"],
        paginate=True,
    )
    payload = server.list_tools(user=None)
    assert isinstance(payload, dict)
    (tool,) = payload["tools"]
    assert tool["name"] == "invoices.list"
    # The wire's merged schema, not the bare serializer: a paginated, orderable
    # selector advertises ``ordering`` / ``page`` / ``limit`` arguments.
    props = tool["inputSchema"]["properties"]
    assert {"ordering", "page", "limit"} <= set(props)


def test_list_tools_paginates_with_an_opaque_cursor(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PAGE_SIZE": 1}
    server = _server()
    for name in ("a", "b"):
        server.register_service_tool(name=name, spec=ServiceSpec(service=lambda: None))

    first = server.list_tools(user=None)
    assert isinstance(first, dict)
    assert [t["name"] for t in first["tools"]] == ["a"]
    cursor = first["nextCursor"]
    assert cursor is not None

    second = server.list_tools(cursor, user=None)
    assert isinstance(second, dict)
    assert [t["name"] for t in second["tools"]] == ["b"]
    assert second.get("nextCursor") is None


def test_list_tools_bad_cursor_is_a_jsonrpc_error() -> None:
    server = _server()
    result = server.list_tools("not-a-real-cursor", user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.INVALID_PARAMS


def test_list_tools_filters_by_listing_permissions(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    server.register_service_tool(
        name="hidden", spec=ServiceSpec(service=lambda: None), permissions=[_Deny()]
    )
    payload = server.list_tools(user=None)
    assert isinstance(payload, dict)
    assert payload["tools"] == []


# ----- acall_tool -----


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_runs_a_service_tool() -> None:
    server = _server()
    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=lambda *, data: Invoice.objects.create(**data),
            input_serializer=InvoiceInputSerializer,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
            ),
        ),
    )
    out = await server.acall_tool(
        "invoices.create", {"number": "A-1", "amount_cents": 100}, user=None
    )
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "A-1"


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_applies_read_extras_the_spec_core_omits() -> None:
    """Ordering + pagination — the read-shaped transport extras ``call_tool``
    (spec core) does not layer on — apply through ``acall_tool``."""
    from asgiref.sync import sync_to_async

    @sync_to_async
    def _setup() -> None:
        for number, cents in (("A", 300), ("B", 100), ("C", 200)):
            Invoice.objects.create(number=number, amount_cents=cents)

    await _setup()
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        ordering_fields=["amount_cents"],
        paginate=True,
    )
    out = await server.acall_tool(
        "invoices.list", {"ordering": "amount_cents", "page": 1, "limit": 2}, user=None
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["totalPages"] == 2
    assert [item["number"] for item in payload["items"]] == ["B", "C"]


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_runs_a_chain_tool() -> None:
    """Chain tools — which the spec-core ``call_tool`` rejects with
    ``TypeError`` — run through the full in-process transport."""
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "made",
                ServiceSpec(
                    service=lambda *, data: Invoice.objects.create(**data),
                    input_serializer=InvoiceInputSerializer,
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
                    ),
                ),
            ),
        ],
    )
    out = await server.acall_tool("chain", {"number": "Z", "amount_cents": 9}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "Z"


async def test_acall_tool_enforces_mcp_permissions() -> None:
    """Transport-level MCP permissions — which the spec core does not consult —
    gate ``acall_tool``."""
    server = _server()
    server.register_service_tool(
        name="locked", spec=ServiceSpec(service=lambda: None), permissions=[_Deny()]
    )
    result = await server.acall_tool("locked", {}, user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_enforces_selector_spec_permissions() -> None:
    """Regression guard: a selector spec's ``permission_classes`` are
    wrapped into ``binding.permissions`` at registration, so the wire path
    (unlike the pre-fix spec-core ``call_tool``) has always denied them."""
    server = _server()
    server.register_selector_tool(
        name="secret",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda **_: {"leaked": True},
            permission_classes=[_DenyAllSpecPerm],
        ),
    )
    result = await server.acall_tool("secret", {"pk": 1}, user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.FORBIDDEN


async def test_acall_tool_unknown_tool_is_a_jsonrpc_error() -> None:
    # ``arguments`` omitted (defaults to ``None`` → ``{}``) and ``request`` omitted
    # (a context is synthesised) — the bare-minimum call shape.
    server = _server()
    result = await server.acall_tool("nope", user=None)
    assert isinstance(result, JsonRpcError)
    assert result.code == JsonRpcErrorCode.TOOL_NOT_FOUND


# ----- _call_context -----


def test_call_context_synthesises_a_request_bearing_the_user() -> None:
    server = _server()
    user = object()
    ctx = server._call_context(user=user)
    assert ctx.http_request.user is user  # type: ignore[attr-defined]
    assert ctx.http_request.method == "POST"
    assert ctx.token.user is user
    assert ctx.tools is server.tools
    # The advertised protocol version tracks the server config, not a literal.
    assert ctx.protocol_version == get_setting("PROTOCOL_VERSIONS")[0]


def test_call_context_uses_a_provided_request_verbatim() -> None:
    server = _server()
    request = HttpRequest()
    request.method = "GET"
    sentinel = object()
    request.user = sentinel  # type: ignore[attr-defined]
    ctx = server._call_context(user=None, request=request)
    # The real request is threaded through untouched (method not forced to POST,
    # its pre-set user left alone) — only synthetic requests are mutated.
    assert ctx.http_request is request
    assert ctx.http_request.method == "GET"
    assert ctx.http_request.user is sentinel  # type: ignore[attr-defined]


# ----- scopes on the in-process surface -----


def test_call_context_carries_scopes() -> None:
    server = _server()
    assert server._call_context(user=None).token.scopes == ()
    assert server._call_context(user=None, scopes=["a", "b"]).token.scopes == ("a", "b")


def test_list_tools_scopes_reveal_a_scope_gated_tool(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    server.register_service_tool(
        name="gated",
        spec=ServiceSpec(service=lambda: None),
        permissions=[ScopeRequired(["invoices:read"])],
    )
    # Without the scope the gated tool is invisible; supplying it reveals the tool.
    assert server.list_tools(user=None)["tools"] == []  # type: ignore[index]
    revealed = server.list_tools(user=None, scopes=["invoices:read"])
    assert isinstance(revealed, dict)
    assert [t["name"] for t in revealed["tools"]] == ["gated"]


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_scopes_allow_a_scope_gated_call() -> None:
    server = _server()
    server.register_service_tool(
        name="gated",
        spec=ServiceSpec(service=lambda: {"ok": True}),
        permissions=[ScopeRequired(["invoices:write"])],
    )
    denied = await server.acall_tool("gated", {}, user=None)
    assert isinstance(denied, JsonRpcError)
    assert denied.code == JsonRpcErrorCode.FORBIDDEN
    allowed = await server.acall_tool("gated", {}, user=None, scopes=["invoices:write"])
    assert isinstance(allowed, dict)
    assert allowed.get("isError") is not True


# ----- alist_tools -----


async def test_alist_tools_mirrors_list_tools() -> None:
    server = _server()
    server.register_service_tool(name="a", spec=ServiceSpec(service=lambda: None))
    assert await server.alist_tools(user=None) == server.list_tools(user=None)


async def test_alist_tools_honors_scopes(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    server = _server()
    server.register_service_tool(
        name="gated",
        spec=ServiceSpec(service=lambda: None),
        permissions=[ScopeRequired(["s"])],
    )
    payload = await server.alist_tools(user=None, scopes=["s"])
    assert isinstance(payload, dict)
    assert [t["name"] for t in payload["tools"]] == ["gated"]


@pytest.mark.django_db(transaction=True)
async def test_alist_tools_runs_db_backed_listing_filter_off_the_loop(settings) -> None:
    """The reason ``alist_tools`` exists: a DB-backed listing permission
    (``DjangoPermRequired`` → ``user.has_perm``) raises ``SynchronousOnlyOperation``
    when the sync ``list_tools`` reaches it from inside the event loop; the async
    variant runs the whole filter in a thread."""
    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user_model
    from django.core.exceptions import SynchronousOnlyOperation

    settings.REST_FRAMEWORK_MCP = {"FILTER_LISTINGS_BY_PERMISSIONS": True}
    user = await sync_to_async(get_user_model().objects.create_user)(username="u")
    server = _server()
    server.register_service_tool(
        name="admin",
        spec=ServiceSpec(service=lambda: None),
        permissions=[DjangoPermRequired("testapp.add_invoice")],
    )
    with pytest.raises(SynchronousOnlyOperation):
        server.list_tools(user=user)
    payload = await server.alist_tools(user=user)
    assert isinstance(payload, dict)
    # The user lacks the perm, so the tool is filtered out — but no exception.
    assert payload["tools"] == []
