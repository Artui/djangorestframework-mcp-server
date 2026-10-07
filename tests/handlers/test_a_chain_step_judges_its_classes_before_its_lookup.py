"""A chain step's permissions answer before its lookup, and its stand-in names the step.

A chain judges every step's wrapped ``permission_classes`` up front, against
stand-ins, and each step judges its spec again when it runs. That second check
ran after the step's ``inputs`` and lookup, so for a caller the step denied a
row that exists answered ``-32006`` and a missing one ``not_found``, and the
lookup ran. It now runs the class-level half against the step's own view before
both, and only the object-level half on the row the step resolves.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_step import ChainStep
from tests.testapp.models import Invoice

_ROUTES = ["handler", "async_handler", "acall_tool"]
_KINDS = ["selector", "service"]

# Row 1 exists and row 2 does not.
_ROWS: dict[int, dict[str, Any]] = {1: {"id": 1}}


class _Args(serializers.Serializer):
    pk = serializers.IntegerField()


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
    if route == "acall_tool":
        return await server.acall_tool("chain", arguments, user=None)
    params: dict[str, Any] = {"name": "chain", "arguments": arguments}
    if route == "async_handler":
        return await handle_tools_call_async(params, _ctx(server))
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


def _fetch_step(
    kind: str, permission: type[BasePermission], lookup: Any, alias: str = "fetch"
) -> ChainStep:
    """A step finding row ``pk``: a ``RETRIEVE`` selector, or a service whose inputs resolve it."""
    if kind == "selector":
        return ChainStep(
            alias,
            SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=lookup, permission_classes=[permission]
            ),
            inputs=lambda ctx: {"data": dict(ctx.args)},
        )
    return ChainStep(
        alias,
        ServiceSpec(
            service=lambda instance=None: {"touched": True}, permission_classes=[permission]
        ),
        # A chain step has no ``instance_selector_spec`` of its own, so its
        # inputs resolve the target.
        inputs=lambda ctx: {"instance": lookup(data=dict(ctx.args))},
    )


# ----- the class-level check comes before the step's inputs and lookup -----


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_chain_step_judges_its_classes_before_its_lookup(route: str, kind: str) -> None:
    # The permission refuses once an earlier step has marked the call, so the
    # up-front stand-in admits and the step's own check is the one that
    # refuses: the shape of any answer that changes while the chain runs.
    marked: list[bool] = []
    looked_up: list[Any] = []

    class _RefusesOnceMarked(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            return not marked

    def _lookup(*, data: Any) -> Any:
        looked_up.append(data["pk"])
        return _ROWS.get(data["pk"])

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Mark, then fetch a row.",
        input_serializer=_Args,
        steps=[
            ChainStep("mark", ServiceSpec(service=lambda: marked.append(True) or {"ok": True})),
            _fetch_step(kind, _RefusesOnceMarked, _lookup),
        ],
    )

    existing = await _via(server, route, {"pk": 1})
    marked.clear()
    missing = await _via(server, route, {"pk": 2})

    assert isinstance(existing, JsonRpcError), f"answered {existing!r}"
    assert isinstance(missing, JsonRpcError), f"answered {missing!r}"
    assert existing.code == missing.code == JsonRpcErrorCode.FORBIDDEN
    assert looked_up == []


def _counting(calls: list[str]) -> type[BasePermission]:
    class _Counting(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            calls.append("class")
            return True

        def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
            calls.append("object")
            return True

    return _Counting


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_chain_step_asks_each_check_once(route: str, kind: str) -> None:
    # Once up front against the stand-in, once against the step's view before
    # its lookup, and the object-level check once on the row it resolves: the
    # check after the lookup runs only the object-level half.
    row: Invoice = await Invoice.objects.acreate(number="A-1")
    calls: list[str] = []
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Fetch an invoice.",
        input_serializer=_Args,
        steps=[
            _fetch_step(kind, _counting(calls), lambda *, data: Invoice.objects.get(pk=data["pk"]))
        ],
    )

    out = await _via(server, route, {"pk": row.pk})

    assert not isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.get("isError") is not True, out
    assert calls == ["class", "class", "object"]


# ----- the up-front stand-in names the step it judges -----


class _RefusesTheFetchAction(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return view.action != "fetch"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
async def test_a_step_permission_refusing_its_action_blocks_the_chain_before_any_step(
    route: str,
) -> None:
    # The step's view carries its alias, and so does the stand-in the up-front
    # check judges, which carried ``None`` and admitted. A non-atomic chain
    # then wrote its first step before the second was refused.
    ran: list[str] = []
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Write, then fetch a row.",
        input_serializer=_Args,
        atomic=False,
        steps=[
            ChainStep("write", ServiceSpec(service=lambda: ran.append("write") or {"ok": True})),
            _fetch_step("selector", _RefusesTheFetchAction, lambda *, data: _ROWS.get(data["pk"])),
        ],
    )

    out = await _via(server, route, {"pk": 1})

    assert isinstance(out, JsonRpcError), f"answered {out!r}"
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    assert ran == []


@pytest.mark.django_db(transaction=True)
async def test_a_chain_steps_stand_in_carries_the_steps_action() -> None:
    # Each step's wrapped classes are judged under the step's alias, and the
    # chain-level permissions under the tool's name, all with the chain's
    # arguments as ``request.data`` and no route or query string.
    seen: list[tuple[Any, ...]] = []

    class _Recording(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(
                (view.action, dict(request.data), dict(view.kwargs), dict(request.query_params))
            )
            return True

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_chain_tool(
        name="chain",
        description="Fetch a row twice.",
        input_serializer=_Args,
        permissions=[DRFPermissionAdapter(_Recording)],
        steps=[
            _fetch_step("selector", _Recording, lambda *, data: _ROWS.get(data["pk"]), "first"),
            _fetch_step("selector", _Recording, lambda *, data: _ROWS.get(data["pk"]), "second"),
        ],
    )

    out = await _via(server, "acall_tool", {"pk": 1})

    assert not isinstance(out, JsonRpcError), f"answered {out!r}"
    # Up front: each step's class under its alias, then the chain's own; then
    # each step's own check against its view as it runs.
    assert [action for action, *_rest in seen] == ["first", "second", "chain", "first", "second"]
    assert {(repr(data), repr(kwargs), repr(query)) for _a, data, kwargs, query in seen} == {
        (repr({"pk": 1}), repr({}), repr({}))
    }
