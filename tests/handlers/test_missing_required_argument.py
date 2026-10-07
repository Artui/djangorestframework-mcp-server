"""A call that leaves out a required argument is a ``validation_error`` result.

A selector -- a selector tool's own, or the lookup a service tool resolves its
target through -- called without a parameter it has no default for raises
``TypeError``. That escaped every handler: the wire answered HTTP 500 with
JSON-RPC ``-32603`` "Internal error", and ``call_tool`` / ``acall_tool`` raised
it to the caller. Now the names the tool's ``inputSchema`` requires of its
selectors are checked before dispatch, and a missing one is answered
``"Missing required argument(s): `pk`."`` -- the sentence the Pydantic-AI toolset
gives the same call -- with ``{"pk": ["This field is required."]}`` under
``detail``, the shape an input serializer gives a missing field. A serializer's
own refusal, a missing field included, still reads ``"Invalid arguments"``.

The check runs after the transport-level permissions, so a caller the listing
hides the tool from is refused for the permission and never learns, from a
missing-argument answer, that the tool exists.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import Client
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    UNSET,
    InputRequired,
    NotClientInput,
    UnsetType,
)
from rest_framework_services.types.pool_seeds import PoolSeeds
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from typing_extensions import TypedDict

from rest_framework_mcp import AgentConventions, ChainStep, MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.constants import ArgumentBinding, JsonRpcErrorCode, UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.conftest import post_jsonrpc
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

MODERN = "2026-07-28"
_REQUIRED = ["This field is required."]


class _NumberInput(serializers.Serializer):
    number = serializers.CharField(max_length=32)


class _PkInput(serializers.Serializer):
    pk = serializers.IntegerField()


class _OptionalPkRequiredNumber(serializers.Serializer):
    pk = serializers.IntegerField(required=False)
    number = serializers.CharField(max_length=32)


class _DefaultedNumber(serializers.Serializer):
    number = serializers.CharField(max_length=32, default="INV-1")


class _PkScope(TypedDict):
    pk: int


class _TenantScope(TypedDict):
    tenant: str


class _MaybeTenantScope(TypedDict):
    # ``UnsetType`` in the value: the provider may decline, so the key does not
    # count as filled.
    tenant: str | UnsetType


def _acme_scope() -> _TenantScope:
    return {"tenant": "acme"}


def _acme_or_decline_scope() -> _MaybeTenantScope:
    return {"tenant": "acme"}


def _declining_scope() -> _MaybeTenantScope:
    return {"tenant": UNSET}


def _by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _by_pk_for_tenant(*, pk: int, tenant: str) -> Any:
    return Invoice.objects.filter(pk=pk, number__startswith=tenant)


def _by_pk_and_number(*, pk: int, number: str) -> Any:
    return Invoice.objects.filter(pk=pk, number=number)


def _by_ids(*, ids: list[int]) -> Any:
    return Invoice.objects.filter(pk__in=ids)


def _by_number_and_pk(*, number: str, pk: int = 0) -> Any:
    # ``pk`` defaulted: registration counts a selector tool's ``input_serializer``
    # fields as the sources of its parameters, and not its ``UrlKwarg`` names.
    return Invoice.objects.filter(pk=pk, number=number)


def _by_number(*, number: str) -> Any:
    return Invoice.objects.filter(number=number)


def _rename(*, instance: Invoice, data: dict[str, Any]) -> Invoice:
    instance.number = data["number"]
    instance.save(update_fields=["number"])
    return instance


def _rename_all(*, collection: Any, data: dict[str, Any]) -> dict[str, int]:
    return {"renamed": collection.update(number=data["number"])}


def _echo(*, data: Any) -> Any:
    return {"count": len(data)}


def _first_invoice_pk() -> dict[str, Any]:
    # Untyped: nothing says which keys it fills.
    return {"pk": Invoice.objects.order_by("pk").values_list("pk", flat=True).first()}


def _typed_first_invoice_pk() -> _PkScope:
    return {"pk": Invoice.objects.order_by("pk").values_list("pk", flat=True).first()}


def _out(kind: SelectorKind = SelectorKind.RETRIEVE) -> SelectorSpec:
    return SelectorSpec(kind=kind, output_serializer=InvoiceOutputSerializer)


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore(), **kwargs
    )
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_by_pk, output_serializer=InvoiceOutputSerializer
        ),
    )
    server.register_service_tool(
        name="rename",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=_NumberInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
            output_selector_spec=_out(),
        ),
    )
    server.register_service_tool(
        name="rename_all",
        spec=ServiceSpec(
            service=_rename_all,
            atomic=False,
            input_serializer=_NumberInput,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_by_ids),
        ),
    )
    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        steps=[ChainStep("made", ServiceSpec(service=_echo, atomic=False))],
    )
    return server


def _ctx(server: MCPServer, pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS) -> MCPCallContext:
    # ``pool_seeds`` is the server's on the wire; a context built by hand has to
    # be handed the same ones, or the seeds neither fill nor outrank anything.
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        pool_seeds=pool_seeds,
        # The server's wording, as the viewsets hand it to the context they
        # build, so a handler route answers in the words the server was given.
        conventions=server.conventions,
    )


async def _call(
    server: MCPServer,
    name: str,
    arguments: Any,
    *,
    is_async: bool,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> Any:
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if is_async:
        return await handle_tools_call_async(params, _ctx(server, pool_seeds))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_tools_call)(params, _ctx(server, pool_seeds))


# Every entry point a tool call takes: the wire's sync and async handlers, and
# the two in-process ones, which reach dispatch by separate code.
_ROUTES = ["handler", "async_handler", "call_tool", "acall_tool"]


async def _via(server: MCPServer, route: str, name: str, arguments: dict[str, Any]) -> Any:
    """The result payload of ``name`` called with ``arguments`` through ``route``."""
    if route == "call_tool":
        return (await sync_to_async(server.call_tool)(name, arguments, user=None)).to_dict()
    if route == "acall_tool":
        return await server.acall_tool(name, arguments, user=None)
    return await _call(server, name, arguments, is_async=route == "async_handler")


def _missing(out: Any) -> dict[str, Any]:
    """The detail of the result a missing argument earns.

    Its message names exactly the names its detail is keyed by, so a caller
    asserting the detail asserts the message with it.
    """
    assert not isinstance(out, JsonRpcError), f"answered as a protocol error: {out!r}"
    error = tool_error(out)
    assert error["type"] == "validation_error"
    names = ", ".join(f"`{name}`" for name in sorted(error["detail"]))
    assert error["message"] == f"Missing required argument(s): {names}."
    return error["detail"]


def _refused(out: Any) -> dict[str, Any]:
    """The detail of a refusal an input serializer answered: the generic message."""
    assert not isinstance(out, JsonRpcError), f"answered as a protocol error: {out!r}"
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Invalid arguments"
    return error["detail"]


# ---------- every route that answers a tool call ----------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    ("name", "arguments", "detail", "read"),
    [
        # A selector tool's own parameter.
        ("get", {}, {"pk": _REQUIRED}, _missing),
        # A service tool's instance lookup, beside a valid serializer field.
        ("rename", {"number": "INV-2"}, {"pk": _REQUIRED}, _missing),
        # A service tool's collection lookup.
        ("rename_all", {"number": "INV-2"}, {"ids": _REQUIRED}, _missing),
        # A chain advertises its input serializer, which answers for itself, in
        # its own words: the missing-argument sentence is the selectors' check.
        ("chain", {"amount_cents": 1}, {"number": _REQUIRED}, _refused),
    ],
)
async def test_a_missing_argument_is_a_validation_error_result(
    name: str, arguments: dict[str, Any], detail: dict[str, Any], read: Any, is_async: bool
) -> None:
    out = await _call(_server(), name, arguments, is_async=is_async)

    assert read(out) == detail


@pytest.mark.django_db
@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-2"})])
def test_call_tool_answers_a_missing_argument_with_a_result(
    name: str, arguments: dict[str, Any]
) -> None:
    result = _server().call_tool(name, arguments, user=None)

    assert _missing(result.to_dict()) == {"pk": _REQUIRED}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-2"})])
async def test_acall_tool_answers_a_missing_argument_with_a_result(
    name: str, arguments: dict[str, Any]
) -> None:
    result = await _server().acall_tool(name, arguments, user=None)

    assert _missing(result) == {"pk": _REQUIRED}


@pytest.mark.django_db
def test_the_missing_argument_is_one_the_schema_requires() -> None:
    """The rule the client is told and the rule enforced are one."""
    server = _server()
    listed: Any = server.list_tools(user=None)
    schemas = {tool["name"]: tool["inputSchema"] for tool in listed["tools"]}

    assert schemas["get"]["required"] == ["pk"]
    assert schemas["rename"]["required"] == ["pk", "number"]
    assert schemas["rename_all"]["required"] == ["ids", "number"]


# ---------- the wire, in both protocol eras ----------


def _post_modern(client: Client, params: dict[str, Any]) -> Any:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return client.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {**params, "_meta": meta}}
        ),
        content_type="application/json",
        headers={
            "Mcp-Protocol-Version": MODERN,
            "Mcp-Method": "tools/call",
            "Mcp-Name": params["name"],
        },
    )


@pytest.fixture
def mounted(settings: Any) -> None:
    """Mount this file's server; listed before ``era_session`` so ``initialize`` reaches it."""
    settings.ROOT_URLCONF = urlconf_for(_server())


@pytest.fixture(params=["2025-11-25", MODERN])
def era_session(request: Any, client: Client) -> str | None:
    if request.param == MODERN:
        return None
    return request.getfixturevalue("initialized_session")


@pytest.mark.django_db
def test_the_wire_serves_a_missing_argument_as_a_result(
    mounted: None, client: Client, era_session: str | None
) -> None:
    """It was HTTP 500 with JSON-RPC ``-32603`` "Internal error" in both eras."""
    params: dict[str, Any] = {"name": "get", "arguments": {}}
    if era_session is None:
        response = _post_modern(client, params)
    else:
        response = post_jsonrpc(client, method="tools/call", params=params, session_id=era_session)

    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    assert _missing(body["result"]) == {"pk": _REQUIRED}


# ---------- after the permission answer ----------


def _guarded_server() -> MCPServer:
    """A tool whose spec admits only an authenticated caller.

    Anyone else is denied, and is told so before anything about the arguments:
    a missing-argument answer would describe a tool the caller may not call.
    """
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[IsAuthenticated],
        ),
    )
    server.register_service_tool(
        name="rename",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=_NumberInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
            output_selector_spec=_out(),
            permission_classes=[IsAuthenticated],
        ),
    )
    return server


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("name", ["get", "rename"])
async def test_a_denied_caller_is_refused_before_the_argument_is_checked(
    name: str, is_async: bool
) -> None:
    out = await _call(_guarded_server(), name, {}, is_async=is_async)

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.parametrize("name", ["get", "rename"])
def test_call_tool_refuses_a_denied_caller_before_the_argument_is_checked(name: str) -> None:
    with pytest.raises(PermissionDenied):
        _guarded_server().call_tool(name, {}, user=None)


def _hidden_url_kwarg_server() -> MCPServer:
    """Both tool kinds, guarded as ``_guarded_server``'s are, on a listing that hides them.

    ``FILTER_LISTINGS_BY_PERMISSIONS`` is on, so a caller the spec denies is not
    told the tools exist. Each takes ``pk`` as a ``UrlKwarg(required=True)``,
    which the channel split refuses before the selectors' own check runs.
    """
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(filter_listings_by_permissions=True),
    )
    pk = (UrlKwarg("pk", type="integer", required=True),)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[IsAuthenticated],
        ),
        url_kwargs=pk,
    )
    server.register_service_tool(
        name="rename",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=_NumberInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
            output_selector_spec=_out(),
            permission_classes=[IsAuthenticated],
        ),
        url_kwargs=pk,
    )
    return server


@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-1"})])
def test_call_tool_refuses_a_denied_caller_before_a_missing_url_kwarg(
    name: str, arguments: dict[str, Any]
) -> None:
    # ``call_tool`` answered the split's refusal before the permission, telling a
    # caller the listing hides the tool from which argument it left out.
    server = _hidden_url_kwarg_server()
    listed: Any = server.list_tools(user=None)
    assert name not in {tool["name"] for tool in listed["tools"]}

    with pytest.raises(PermissionDenied):
        server.call_tool(name, arguments, user=None)


@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-1"})])
async def test_every_other_route_refuses_a_denied_caller_before_a_missing_url_kwarg(
    name: str, arguments: dict[str, Any], route: str
) -> None:
    server = _hidden_url_kwarg_server()
    if route == "acall_tool":
        out: Any = await server.acall_tool(name, arguments, user=None)
    else:
        out = await _call(server, name, arguments, is_async=route == "async_handler")

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


class _AcmeRoute(BasePermission):
    """Grants a ``call_tool`` call whose route names ``acme``, as a view reads a route.

    ``tenant`` is a route capture, so ``call_tool`` puts it in ``view.kwargs``
    and leaves it out of ``request.data``. A context built without the URL
    kwargs the call delivered has no ``tenant`` in ``view.kwargs``, and one
    built from the raw arguments has it in ``request.data``: either denies.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        return view.kwargs.get("tenant") == "acme" and "tenant" not in request.data


def _acme_route_server() -> MCPServer:
    """Both tool kinds behind ``_AcmeRoute``, routed by a required ``pk`` and a ``tenant``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    route = (UrlKwarg("pk", type="integer", required=True), UrlKwarg("tenant"))
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[_AcmeRoute],
        ),
        url_kwargs=route,
    )
    server.register_service_tool(
        name="rename",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=_NumberInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
            output_selector_spec=_out(),
            permission_classes=[_AcmeRoute],
        ),
        url_kwargs=route,
    )
    return server


@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-1"})])
def test_call_tool_checks_a_missing_url_kwargs_permission_against_the_delivered_route(
    name: str, arguments: dict[str, Any]
) -> None:
    # ``pk`` is missing and ``tenant`` delivered, so the permission answering
    # first sees the request and view the call would have run with, grants,
    # and the missing argument is what the caller is told.
    result = _acme_route_server().call_tool(name, {**arguments, "tenant": "acme"}, user=None)

    assert _missing(result.to_dict()) == {"pk": _REQUIRED}


@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-1"})])
def test_call_tool_denies_a_missing_url_kwargs_call_its_route_does_not_grant(
    name: str, arguments: dict[str, Any]
) -> None:
    # The other half: the permission reads ``tenant`` rather than granting every
    # call, so the test above holds the context it was shown.
    with pytest.raises(PermissionDenied):
        _acme_route_server().call_tool(name, {**arguments, "tenant": "beta"}, user=None)


@pytest.mark.parametrize("is_async", [False, True])
async def test_a_selector_tools_input_serializer_answers_first(is_async: bool) -> None:
    # Its own refusal comes first, as it did before; the selector's check sees
    # only arguments the serializer accepted, so the two never answer together.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk_and_number,
            output_serializer=InvoiceOutputSerializer,
        ),
        input_serializer=_OptionalPkRequiredNumber,
    )

    neither = await _call(server, "get", {}, is_async=is_async)
    no_pk = await _call(server, "get", {"number": "INV-1"}, is_async=is_async)

    assert _refused(neither) == {"number": _REQUIRED}
    assert _missing(no_pk) == {"pk": _REQUIRED}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_several_missing_names_are_sorted_and_joined(route: str) -> None:
    # The selector declares ``pk`` before ``number``, so the check finds them in
    # that order; the sentence sorts them, as the Pydantic-AI toolset does, so
    # one omission reads the same on every call and on both transports.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk_and_number,
            output_serializer=InvoiceOutputSerializer,
        ),
    )

    out = await _via(server, route, "get", {})

    error = tool_error(out)
    assert error["message"] == "Missing required argument(s): `number`, `pk`."
    assert error["detail"] == {"pk": _REQUIRED, "number": _REQUIRED}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_service_tools_serializer_keeps_the_generic_message_for_its_own_field(
    route: str,
) -> None:
    # The lookup's ``pk`` is sent, so the selectors' check passes and the input
    # serializer answers for ``number``: a missing field, but the serializer's
    # refusal rather than the selectors' check, so "Invalid arguments".
    invoice = await Invoice.objects.acreate(number="INV-1")

    out = await _via(_server(), route, "rename", {"pk": invoice.pk})

    assert _refused(out) == {"number": _REQUIRED}


class _OptionalPkShortNumber(serializers.Serializer):
    pk = serializers.IntegerField(required=False)
    number = serializers.CharField(max_length=3)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "acall_tool"])
async def test_a_refused_value_beside_a_missing_argument_keeps_the_generic_message(
    route: str,
) -> None:
    # ``pk`` is missing and ``number`` is refused. A selector tool's serializer
    # answers first, so the refusal is the serializer's and reads "Invalid
    # arguments", with only its own field in the detail. ``call_tool`` is not
    # among the routes: it does not run this serializer.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk_and_number,
            output_serializer=InvoiceOutputSerializer,
        ),
        input_serializer=_OptionalPkShortNumber,
    )

    out = await _via(server, route, "get", {"number": "INV-0001"})

    assert list(_refused(out)) == ["number"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_missing_url_kwarg_is_answered_before_a_selector_tools_input_serializer(
    route: str,
) -> None:
    # The channel split runs before the serializer, so a call missing both a
    # ``UrlKwarg(required=True)`` and a serializer field is told about the URL
    # kwarg alone; the serializer would have answered ``number``.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_number_and_pk,
            output_serializer=InvoiceOutputSerializer,
        ),
        input_serializer=_NumberInput,
        url_kwargs=(UrlKwarg("pk", type="integer", required=True),),
    )

    out = await _via(server, route, "get", {})

    assert _missing(out) == {"pk": _REQUIRED}


# ---------- what is not refused ----------


def _url_kwarg_server(*, required: bool = False, **server_kwargs: Any) -> MCPServer:
    """Both tool kinds, each taking ``pk`` as a ``UrlKwarg`` with no default."""
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=None, **server_kwargs
    )
    pk = (UrlKwarg("pk", type="integer", required=required),)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_by_pk, output_serializer=InvoiceOutputSerializer
        ),
        url_kwargs=pk,
    )
    server.register_service_tool(
        name="rename",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=_NumberInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
            output_selector_spec=_out(),
        ),
        url_kwargs=pk,
    )
    return server


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["handler", "async_handler", "call_tool", "acall_tool"])
@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-1"})])
async def test_a_url_kwarg_carrying_the_argument_satisfies_it(
    name: str, arguments: dict[str, Any], route: str
) -> None:
    # Popped out of the arguments into ``view.kwargs``, from where dispatch
    # spreads it into the selector's pool: present, though not in the params.
    invoice = await Invoice.objects.acreate(number="INV-1")

    out = await _via(_url_kwarg_server(), route, name, {**arguments, "pk": invoice.pk})

    assert out["structuredContent"]["number"] == "INV-1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(("name", "arguments"), [("get", {}), ("rename", {"number": "INV-1"})])
async def test_a_missed_required_url_kwarg_is_answered_in_the_same_shape(
    name: str, arguments: dict[str, Any], route: str
) -> None:
    # Refused by the channel split rather than by the selectors' check, and
    # answered as that check answers, so a client reads one shape for a missing
    # argument whichever way the parameter was declared. It was
    # ``non_field_errors`` "Missing required argument(s): 'pk'." under
    # "Service validation error.".
    out = await _via(_url_kwarg_server(required=True), route, name, arguments)

    assert _missing(out) == {"pk": _REQUIRED}


@pytest.mark.parametrize("is_async", [False, True])
async def test_a_null_url_kwarg_is_a_missing_argument(is_async: bool) -> None:
    # A null route capture is an omitted one, so nothing reaches the selector.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_by_pk, output_serializer=InvoiceOutputSerializer
        ),
        url_kwargs=(UrlKwarg("pk", type="integer"),),
    )

    out = await _call(server, "get", {"pk": None}, is_async=is_async)

    assert _missing(out) == {"pk": _REQUIRED}


def _by_pk_in_tenant(*, pk: int, tenant: str) -> Any:
    # Names ``tenant`` plainly and with no default, so the lookup alone would
    # require it of the caller.
    return Invoice.objects.filter(pk=pk)


def _same_tenant(*, tenant: Annotated[str, NotClientInput] = "acme") -> None:
    # Owns ``tenant`` for the whole call (``server_owned_keys``).
    return None


class _NumberAndTenantInput(_NumberInput):
    # Optional, so the serializer does not refuse a call leaving it out.
    tenant = serializers.CharField(required=False)


def _owned_tenant_server(input_serializer: type[serializers.Serializer]) -> MCPServer:
    """A service tool whose lookup requires ``tenant``, which its precondition owns."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="rename",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=input_serializer,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_by_pk_in_tenant
            ),
            preconditions=[_same_tenant],
            output_selector_spec=_out(),
        ),
    )
    return server


def _rename_schema(server: MCPServer) -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return next(tool for tool in listed["tools"] if tool["name"] == "rename")["inputSchema"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_lookup_key_the_server_owns_is_not_asked_of_the_caller(route: str) -> None:
    # The precondition marks ``tenant`` ``NotClientInput``, so drf-services drops
    # the caller's value before the lookup reads it, and the tool's schema does
    # not advertise it. Nothing the server supplies fills it either, which is the
    # author's gap: no resend could deliver it, so the call is not refused
    # naming it, and fails as the lookup's own ``TypeError``, as drf-services
    # answers a server-side gap. It was refused as
    # "Missing required argument(s): `tenant`.", for a key the caller cannot send.
    invoice = await Invoice.objects.acreate(number="INV-1")
    server = _owned_tenant_server(_NumberInput)

    assert "tenant" not in _rename_schema(server)["properties"]
    with pytest.raises(TypeError, match="tenant"):
        await _via(server, route, "rename", {"pk": invoice.pk, "number": "INV-2"})


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_an_owned_lookup_key_a_field_declares_is_still_asked_of_the_caller(
    route: str,
) -> None:
    # A field of the owned name is the caller's input, so the schema keeps the
    # lookup's ``tenant`` required, and the call is refused for it as the
    # schema says. Holds the subtraction's limit to the names the
    # ``input_serializer`` does not list.
    invoice = await Invoice.objects.acreate(number="INV-1")
    server = _owned_tenant_server(_NumberAndTenantInput)

    assert "tenant" in _rename_schema(server)["required"]
    out = await _via(server, route, "rename", {"pk": invoice.pk, "number": "INV-2"})

    assert _missing(out) == {"tenant": _REQUIRED}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_value_the_input_serializer_defaults_satisfies_it(is_async: bool) -> None:
    # A selector tool's own ``input_serializer`` overlays its validated values on
    # the selector's params, so its default reaches the selector.
    await Invoice.objects.acreate(number="INV-1")

    out = await _call(_defaulted_number_server(), "get", {}, is_async=is_async)

    assert out["structuredContent"]["number"] == "INV-1"


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_runs_the_input_serializer_so_its_default_satisfies_it() -> None:
    await Invoice.objects.acreate(number="INV-1")

    out = await _defaulted_number_server().acall_tool("get", {}, user=None)

    assert out["structuredContent"]["number"] == "INV-1"


@pytest.mark.django_db
def test_call_tool_refuses_a_name_only_the_input_serializer_it_skips_would_fill() -> None:
    # ``call_tool`` does not run a selector tool's MCP-only ``input_serializer``,
    # so its default never reaches the selector on that route: the name is
    # missing there, and is refused rather than raised as the selector's
    # ``TypeError``.
    result = _defaulted_number_server().call_tool("get", {}, user=None)

    assert _missing(result.to_dict()) == {"number": _REQUIRED}


def _defaulted_number_server() -> MCPServer:
    """A selector tool whose ``input_serializer`` defaults the selector's ``number``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_number,
            output_serializer=InvoiceOutputSerializer,
        ),
        input_serializer=_DefaultedNumber,
    )
    return server


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("provider", [_first_invoice_pk, _typed_first_invoice_pk])
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_value_the_provider_fills_satisfies_it(provider: Any, is_async: bool) -> None:
    # Typed, its keys are supplied and nothing asks for ``pk``. Untyped, nothing
    # is checked before the call, because only the assembled pool can tell.
    await Invoice.objects.acreate(number="INV-1")
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk,
            output_serializer=InvoiceOutputSerializer,
            kwargs=provider,
        ),
    )

    out = await _call(server, "get", {}, is_async=is_async)

    assert out["structuredContent"]["number"] == "INV-1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_seed_the_client_sends_anyway_is_admitted_and_outranked(is_async: bool) -> None:
    # ``tenant`` is not advertised, because the server fills it, but the
    # unknown-argument check still knows it: a client sending one is not refused
    # under ``REJECT``, and the seed's value is the one the selector reads.
    invoice = await Invoice.objects.acreate(number="acme-1")
    seeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=None, pool_seeds=seeds
    )
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_by_pk_for_tenant,
            output_serializer=InvoiceOutputSerializer,
        ),
        input_serializer=_PkInput,
        unknown_arguments=UnknownArguments.REJECT,
    )

    sent = await _call(
        server, "get", {"pk": invoice.pk, "tenant": "other"}, is_async=is_async, pool_seeds=seeds
    )
    omitted = await _call(server, "get", {"pk": invoice.pk}, is_async=is_async, pool_seeds=seeds)

    assert sent["structuredContent"]["number"] == "acme-1"
    assert omitted["structuredContent"]["number"] == "acme-1"


def _tenant_server(
    provider: Any, *, selector: Any = _by_pk_for_tenant, **registration: Any
) -> MCPServer:
    """``get(*, pk, tenant)`` with ``provider`` as its ``kwargs=``."""
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=selector,
            output_serializer=InvoiceOutputSerializer,
            kwargs=provider,
        ),
        **registration,
    )
    return server


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_under_caller_wins_a_provider_filled_name_is_not_refused(route: str) -> None:
    # The provider's key is advertised under ``SPREAD_CALLER_WINS``, because the
    # caller's value outranks it, but it is not required: the provider fills it
    # when the caller sends nothing.
    acme = await Invoice.objects.acreate(number="acme-1")
    beta = await Invoice.objects.acreate(number="beta-1")
    server = _tenant_server(_acme_scope, argument_binding=ArgumentBinding.SPREAD_CALLER_WINS)

    filled = await _via(server, route, "get", {"pk": acme.pk})
    sent = await _via(server, route, "get", {"pk": beta.pk, "tenant": "beta"})

    assert filled["structuredContent"]["number"] == "acme-1"
    assert sent["structuredContent"]["number"] == "beta-1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_under_author_wins_the_providers_value_outranks_the_callers(route: str) -> None:
    # The default binding, and the reason the schema hides the provider's key
    # there: a ``tenant`` the caller sends is admitted and then overwritten, so
    # the selector scopes to ``acme`` whatever the caller asked for.
    acme = await Invoice.objects.acreate(number="acme-1")

    out = await _via(_tenant_server(_acme_scope), route, "get", {"pk": acme.pk, "tenant": "beta"})

    assert out["structuredContent"]["number"] == "acme-1"


def _touch(*, instance: Invoice, **_: Any) -> Invoice:
    return instance


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("binding", list(ArgumentBinding))
async def test_a_target_lookups_provider_outranks_the_caller_under_every_binding(
    binding: ArgumentBinding, route: str
) -> None:
    # drf-services lays a target lookup's provider over the arguments whatever
    # the binding says, so ``tenant`` is neither advertised nor the caller's to
    # set: a ``beta`` sent anyway is overwritten and the lookup scopes to ``acme``.
    acme = await Invoice.objects.acreate(number="acme-1")
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="touch",
        spec=ServiceSpec(
            service=_touch,
            atomic=False,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_by_pk_for_tenant, kwargs=_acme_scope
            ),
            output_selector_spec=_out(),
        ),
        argument_binding=binding,
    )
    listed: Any = server.list_tools(user=None)

    out = await _via(server, route, "touch", {"pk": acme.pk, "tenant": "beta"})

    assert set(listed["tools"][0]["inputSchema"]["properties"]) == {"pk"}
    assert out["structuredContent"]["number"] == "acme-1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_key_the_provider_may_decline_is_not_refused(route: str) -> None:
    # Annotated as possibly ``UNSET``, so it is not counted as filled and stays
    # advertised, but a call leaving it out is not refused: this provider fills it.
    acme = await Invoice.objects.acreate(number="acme-1")

    out = await _via(_tenant_server(_acme_or_decline_scope), route, "get", {"pk": acme.pk})

    assert out["structuredContent"]["number"] == "acme-1"


def _by_pk_for_marked_tenant(*, pk: int, tenant: Annotated[str, InputRequired]) -> Any:
    return _by_pk_for_tenant(pk=pk, tenant=tenant)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_marked_key_the_provider_may_decline_is_not_refused(route: str) -> None:
    # The marker keeps ``tenant`` in the schema's ``required`` (the schema
    # tests), but only the assembled pool can say whether it arrived, so the
    # call is not refused before the provider runs; this provider fills it.
    acme = await Invoice.objects.acreate(number="acme-1")
    server = _tenant_server(_acme_or_decline_scope, selector=_by_pk_for_marked_tenant)

    out = await _via(server, route, "get", {"pk": acme.pk})

    assert out["structuredContent"]["number"] == "acme-1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_under_caller_wins_a_marked_provider_key_is_neither_required_nor_refused(
    route: str,
) -> None:
    # The marker says ``tenant`` is required, and the provider fills it when the
    # caller sends none, so under ``SPREAD_CALLER_WINS`` it is offered rather
    # than required, marker or not, and a call without it is served.
    acme = await Invoice.objects.acreate(number="acme-1")
    server = _tenant_server(
        _acme_scope,
        selector=_by_pk_for_marked_tenant,
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
    )
    listed: Any = server.list_tools(user=None)

    out = await _via(server, route, "get", {"pk": acme.pk})

    assert listed["tools"][0]["inputSchema"]["required"] == ["pk"]
    assert out["structuredContent"]["number"] == "acme-1"


def _untyped_empty_scope(**_: Any) -> dict[str, Any]:
    return {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("provider", [_declining_scope, _untyped_empty_scope])
async def test_a_marked_parameter_a_provider_might_fill_is_drf_services_to_refuse(
    route: str, provider: Any
) -> None:
    # Neither provider says it fills ``tenant`` for certain, so the check here
    # skips it, and drf-services' own marker check answers once the pool is
    # assembled, in its own shape: the one case the CHANGELOG names as keeping
    # "Service validation error." with ``non_field_errors``.
    server = _tenant_server(provider, selector=_by_pk_for_marked_tenant)

    out = await _via(server, route, "get", {"pk": 1})

    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Service validation error."
    assert error["detail"] == {"non_field_errors": ["Missing required argument(s): 'tenant'."]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_key_the_provider_declines_is_the_callers_to_send(route: str) -> None:
    # Declining removes the key from the pool, so the caller's value is the one
    # the selector reads, under the default ``SPREAD_AUTHOR_WINS``.
    beta = await Invoice.objects.acreate(number="beta-1")

    out = await _via(
        _tenant_server(_declining_scope), route, "get", {"pk": beta.pk, "tenant": "beta"}
    )

    assert out["structuredContent"]["number"] == "beta-1"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_key_the_provider_declines_and_the_caller_leaves_out_is_refused_in_dispatch(
    route: str,
) -> None:
    # This server does not refuse the call, because only the assembled pool can
    # say whether ``tenant`` arrived. drf-services can, once the provider has
    # declined: the caller could have sent it, so dispatch refuses the call
    # before the selector runs, in its own shape, where the selector used to
    # raise ``TypeError`` and the call failed as a server fault.
    out = await _via(_tenant_server(_declining_scope), route, "get", {"pk": 1})

    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["message"] == "Service validation error."
    assert error["detail"] == {"non_field_errors": ["Missing required argument(s): 'tenant'."]}


# ---------- in the server's own words ----------


_WORDED = AgentConventions(missing_arguments="Left out: {names}.")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    "build",
    [
        # A selector parameter, refused inside dispatch: the sync selector
        # sibling's ``except`` arm answers the handler, the async one
        # ``async_handler`` and ``acall_tool``.
        lambda: _server(conventions=_WORDED),
        # A ``UrlKwarg(required=True)``, refused by the channel split before
        # dispatch: in the request-building step both selector siblings share,
        # and in ``call_tool``'s own split ahead of its permission check.
        lambda: _url_kwarg_server(required=True, conventions=_WORDED),
    ],
    ids=["selector-parameter", "required-url-kwarg"],
)
async def test_a_selector_tools_missing_argument_is_worded_by_the_server(
    build: Any, route: str
) -> None:
    # Each of these four sites builds its result from the context's
    # conventions, and none was reached by a server with wording of its own: a
    # default instance in place of ``conventions`` passed everything else.
    out = await _via(build(), route, "get", {})

    error = tool_error(out)
    assert error["message"] == "Left out: `pk`."
    assert error["detail"] == {"pk": _REQUIRED}
