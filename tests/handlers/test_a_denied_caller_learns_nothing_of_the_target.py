"""A caller the spec's permission denies learns nothing about the target it named.

A spec's ``has_permission`` may read what only the dispatch view carries: the
call's ``request.data``, its ``query_params``, or ``view.action``, which is the
tool's name there and ``None`` on the stand-in the binding's wrapped check
judges. Such a permission was judged on the wire only by the target guard, after
the lookup, so a denied caller was answered ``-32006`` for a row that exists,
``not_found`` for one that does not, and told the name of an argument it left
out. ``call_tool`` already judged it before dispatch; every route now does, and
the target guard is narrowed to the object-level check, so ``has_permission``
still runs once per check rather than once more per call.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from tests.testapp.models import Invoice

_ROUTES = ["handler", "async_handler", "acall_tool", "call_tool"]
_KINDS = ["service", "selector"]

# Row 1 exists and row 2 does not.
_ROWS: dict[int, dict[str, Any]] = {1: {"id": 1}}


class _RefusesTheDispatchedAction(BasePermission):
    """Refuses the call the dispatch view describes, and admits the stand-in.

    The binding's wrapped check judges a view whose ``action`` is ``None``, so it
    admits; the dispatch view carries the tool's name, so the spec's own check
    refuses. The shape of any permission reading what only dispatch carries.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        return view.action != "tool"


class _Row(serializers.Serializer):
    id = serializers.IntegerField()


def _server(kind: str, permission: type[BasePermission] | None, lookup: Any) -> MCPServer:
    """One tool named ``tool``, of ``kind``, whose target ``lookup`` finds by ``pk``.

    ``permission=None`` declares ``permission_classes=None``.
    """
    permission_classes: list[type[BasePermission]] | None = (
        None if permission is None else [permission]
    )
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    if kind == "service":
        server.register_service_tool(
            name="tool",
            description="Touch a row.",
            spec=ServiceSpec(
                service=lambda instance=None: {"touched": True},
                permission_classes=permission_classes,
                instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lookup),
            ),
        )
    else:
        server.register_selector_tool(
            name="tool",
            description="Read a row.",
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lookup,
                output_serializer=_Row,
                permission_classes=permission_classes,
            ),
        )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )


async def _via(server: MCPServer, route: str, arguments: dict[str, Any]) -> Any:
    """What calling ``tool`` through ``route`` answers, a denial read as ``-32006``."""
    if route == "call_tool":
        try:
            result: Any = await sync_to_async(server.call_tool)("tool", arguments, user=None)
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        return result.to_dict()
    if route == "acall_tool":
        return await server.acall_tool("tool", arguments, user=None)
    params: dict[str, Any] = {"name": "tool", "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_denied_caller_is_answered_alike_for_a_row_that_exists_and_one_that_does_not(
    route: str, kind: str
) -> None:
    looked_up: list[Any] = []

    def _lookup(*, pk: int) -> Any:
        looked_up.append(pk)
        return _ROWS.get(pk)

    server = _server(kind, _RefusesTheDispatchedAction, _lookup)

    existing = await _via(server, route, {"pk": 1})
    missing = await _via(server, route, {"pk": 2})

    assert isinstance(existing, JsonRpcError), f"answered {existing!r}"
    assert isinstance(missing, JsonRpcError), f"answered {missing!r}"
    assert (existing.code, existing.message) == (missing.code, missing.message)
    assert existing.code == JsonRpcErrorCode.FORBIDDEN
    # Judged before the lookup, so the lookup leaks nothing either.
    assert looked_up == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_denied_caller_is_not_told_which_argument_it_left_out(
    route: str, kind: str
) -> None:
    server = _server(kind, _RefusesTheDispatchedAction, lambda *, pk: _ROWS.get(pk))

    out = await _via(server, route, {})

    assert isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    assert "pk" not in repr(out)


# ----- the target guard runs only what the up-front check did not -----


def _counting(calls: list[str]) -> type[BasePermission]:
    """A permission admitting everything and recording each check it is asked."""

    class _Counting(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            calls.append("class")
            return True

        def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
            calls.append("object")
            return True

    return _Counting


def _refusing_the_row(calls: list[str]) -> type[BasePermission]:
    """A permission admitting every caller and refusing every row."""

    class _RefusingTheRow(BasePermission):
        def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
            calls.append("object")
            return False

    return _RefusingTheRow


# The wire judges the binding's wrapped copy of the class as well, which
# ``call_tool`` does not consult; the up-front check is one more on every route.
_CLASS_CHECKS: dict[str, int] = {
    "handler": 2,
    "async_handler": 2,
    "acall_tool": 2,
    "call_tool": 1,
}


def _invoice(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_the_class_level_check_is_not_run_again_on_the_resolved_row(
    route: str, kind: str
) -> None:
    row: Invoice = await Invoice.objects.acreate(number="A-1")
    calls: list[str] = []
    server = _server(kind, _counting(calls), _invoice)

    out = await _via(server, route, {"pk": row.pk})

    assert not isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.get("isError") is not True, out
    assert calls == ["class"] * _CLASS_CHECKS[route] + ["object"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_the_narrowed_guard_still_refuses_a_row_the_object_check_denies(
    route: str, kind: str
) -> None:
    row: Invoice = await Invoice.objects.acreate(number="A-1")
    calls: list[str] = []
    server = _server(kind, _refusing_the_row(calls), _invoice)

    out = await _via(server, route, {"pk": row.pk})

    assert isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    assert calls == ["object"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_spec_with_no_permission_classes_is_guarded_by_nothing(
    route: str, kind: str
) -> None:
    # ``permission_classes=None`` inherits a view's classes over HTTP, and off
    # HTTP there is no view to inherit from, so nothing judges the row.
    row: Invoice = await Invoice.objects.acreate(number="A-1")
    server = _server(kind, None, _invoice)

    out = await _via(server, route, {"pk": row.pk})

    assert not isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.get("isError") is not True, out


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_list_target_is_not_judged_row_by_row(route: str) -> None:
    # A LIST resolves a queryset, which no ``has_object_permission`` is asked
    # about: object permissions are a per-row concept, as drf-services rules.
    await Invoice.objects.acreate(number="A-1")
    calls: list[str] = []
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="tool",
        description="List rows.",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Invoice.objects.all(),
            output_serializer=_Row,
            permission_classes=[_refusing_the_row(calls)],
        ),
    )

    out = await _via(server, route, {})

    assert not isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.get("isError") is not True, out
    assert calls == []


class _Counted(serializers.Serializer):
    count = serializers.IntegerField()


class _LookedUpAndCounted(_Counted):
    pk = serializers.IntegerField()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_denied_caller_is_refused_before_its_input_is_validated(
    route: str, kind: str
) -> None:
    # A validation error would describe the input the tool accepts.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda *, pk: _ROWS.get(pk))
    if kind == "service":
        server.register_service_tool(
            name="tool",
            description="Touch a row.",
            spec=ServiceSpec(
                service=lambda *, data, instance=None: {"touched": True},
                input_serializer=_Counted,
                permission_classes=[_RefusesTheDispatchedAction],
                instance_selector_spec=lookup,
            ),
        )
    else:
        server.register_selector_tool(
            name="tool",
            description="Read a row.",
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda *, pk, count: _ROWS.get(pk),
                output_serializer=_Row,
                permission_classes=[_RefusesTheDispatchedAction],
            ),
            input_serializer=_LookedUpAndCounted,
        )

    out = await _via(server, route, {"pk": 1, "count": "many"})

    assert isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.code == JsonRpcErrorCode.FORBIDDEN
