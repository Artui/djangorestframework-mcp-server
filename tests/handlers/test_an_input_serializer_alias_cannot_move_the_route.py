"""An ``input_serializer`` field sourcing a URL kwarg's name cannot move the route.

**Selector tools.** A URL kwarg reaches the selector only as the route judged.

The permission judges the URL kwargs a call delivers, and the split removes
their names from the arguments, so the value travels only through
``view.kwargs``. A selector tool then lays its ``input_serializer``'s validated
values back over the arguments, and a field bound with ``source=`` puts its
value under the source's name: ``project = IntegerField(source="project_pk")``
put a ``project_pk`` back. Under ``SPREAD_CALLER_WINS`` drf-services ranks the
arguments above ``view.kwargs``, so the selector read the caller's project
where the permission had judged another, and under either spreading binding a
call leaving the kwarg out had the caller's value read in its place, on a
route the permission judged as naming no project. Names a ``UrlKwarg`` declares
are dropped from the selector's arguments after the overlay, so the route the
permission judged is the one the selector reads, under every binding.

**Service tools**, as ``docs/concepts.md`` states beside the bindings. A URL
kwarg does not reach a service's pool at all, only ``view.kwargs``, so a
``spec.kwargs`` provider copying it into the pool is a provider like any other,
which the caller's value outranks under ``SPREAD_CALLER_WINS``. The target
lookup reads ``view.kwargs`` under every binding, so it is where a service that
must act on the judged route takes it from.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ArgumentBinding, MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext

_SPREADING = pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS],
    ids=["author-wins", "caller-wins"],
)


class _AliasInput(serializers.Serializer):
    """Validates the argument ``project`` into ``project_pk``, the URL kwarg's name."""

    project = serializers.IntegerField(source="project_pk", required=False)


def _admitting_seven_or_none(seen: list[dict[str, Any]]) -> type[BasePermission]:
    """Admits project 7, or a route naming no project, as a project picker would."""

    class _AdmitsSevenOrNone(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return view.kwargs.get("project_pk", 7) == 7

    return _AdmitsSevenOrNone


def _server(
    binding: ArgumentBinding,
    permission: type[BasePermission],
    read: list[Any],
    url_kwarg: UrlKwarg,
    input_serializer: type[serializers.Serializer] = _AliasInput,
) -> MCPServer:
    def _project(*, project_pk: Any = None, **extras: Any) -> dict[str, Any]:
        read.append(project_pk)
        return {"project_pk": project_pk}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="read_project",
        description="Read a project.",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_project, permission_classes=[permission]
        ),
        url_kwargs=(url_kwarg,),
        input_serializer=input_serializer,
        argument_binding=binding,
    )
    return server


async def _call(
    server: MCPServer, arguments: dict[str, Any], *, is_async: bool, name: str = "read_project"
) -> Any:
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
    )
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if is_async:
        return await handle_tools_call_async(params, context)
    return await sync_to_async(handle_tools_call)(params, context)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@_SPREADING
async def test_a_serializer_field_sourcing_a_url_kwarg_does_not_move_the_route(
    is_async: bool, binding: ArgumentBinding
) -> None:
    # Judged on project 7, read on project 8 under ``SPREAD_CALLER_WINS``.
    # ``SPREAD_AUTHOR_WINS`` already ranked ``view.kwargs`` above the
    # arguments; the case pins that the drop leaves it reading the route.
    seen: list[dict[str, Any]] = []
    read: list[Any] = []
    server = _server(
        binding,
        _admitting_seven_or_none(seen),
        read,
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = await _call(server, {"project_pk": 7, "project": 8}, is_async=is_async)

    assert out.get("isError") is not True, f"answered {out!r}"
    assert read == [7]
    # Judged by the binding's check and again by the target guard, on 7 each time.
    assert seen == [{"project_pk": 7}, {"project_pk": 7}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@_SPREADING
async def test_a_serializer_field_sourcing_a_url_kwarg_left_out_does_not_fill_the_route(
    is_async: bool, binding: ArgumentBinding
) -> None:
    # The call names no project, which the permission admits, and the alias
    # named project 8: with no ``view.kwargs`` value to outrank it, the
    # selector read 8 under both bindings. A caller choosing the project does
    # so through the URL kwarg, which the permission then judges.
    seen: list[dict[str, Any]] = []
    read: list[Any] = []
    server = _server(
        binding, _admitting_seven_or_none(seen), read, UrlKwarg("project_pk", type="integer")
    )

    out = await _call(server, {"project": 8}, is_async=is_async)

    assert out.get("isError") is not True, f"answered {out!r}"
    assert read == [None]
    assert seen == [{}, {}]


class _SameNameInput(serializers.Serializer):
    """A field declared under the URL kwarg's own name, coercing and defaulting it."""

    project_pk = serializers.IntegerField(default=5)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@_SPREADING
async def test_a_field_named_after_a_url_kwarg_leaves_the_selector_reading_the_route(
    is_async: bool, binding: ArgumentBinding
) -> None:
    # The field validates the route's ``"7"`` into ``7``. Laid back, the
    # selector read the coerced value under ``SPREAD_CALLER_WINS`` only, and
    # ``view.kwargs``' value under ``SPREAD_AUTHOR_WINS``; it now reads the
    # route under both.
    read: list[Any] = []
    server = _server(
        binding,
        AllowAny,
        read,
        UrlKwarg("project_pk", type="integer", required=True),
        _SameNameInput,
    )

    out = await _call(server, {"project_pk": "7"}, is_async=is_async)

    assert out.get("isError") is not True, f"answered {out!r}"
    assert read == ["7"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@_SPREADING
async def test_a_field_named_after_a_url_kwarg_does_not_default_a_route_left_out(
    is_async: bool, binding: ArgumentBinding
) -> None:
    # The route names no project, and the field's default named project 5,
    # which the selector read under both bindings without it being judged.
    read: list[Any] = []
    server = _server(
        binding,
        AllowAny,
        read,
        UrlKwarg("project_pk", type="integer"),
        _SameNameInput,
    )

    out = await _call(server, {}, is_async=is_async)

    assert out.get("isError") is not True, f"answered {out!r}"
    assert read == [None]


# ----- service tools: the documented precedence, pinned -----


def _archive_target(*, instance: Any = None, **extras: Any) -> dict[str, Any]:
    return {"archived": instance}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("binding", "ran_on"),
    [(ArgumentBinding.SPREAD_AUTHOR_WINS, 7), (ArgumentBinding.SPREAD_CALLER_WINS, 8)],
    ids=["author-wins", "caller-wins"],
)
async def test_a_provider_copying_a_route_kwarg_is_overridable_under_caller_wins(
    binding: ArgumentBinding, ran_on: int
) -> None:
    ran: list[Any] = []

    def _archive(*, project_pk: Any = None, **extras: Any) -> dict[str, Any]:
        ran.append(project_pk)
        return {"archived": project_pk}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="archive_project",
        description="Archive a project.",
        spec=ServiceSpec(
            service=_archive,
            input_serializer=_AliasInput,
            kwargs=lambda view, request: {"project_pk": view.kwargs.get("project_pk")},
        ),
        url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
        argument_binding=binding,
    )

    out = await _call(
        server, {"project_pk": 7, "project": 8}, is_async=True, name="archive_project"
    )

    assert out.get("isError") is not True, f"answered {out!r}"
    assert ran == [ran_on]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "binding",
    [
        ArgumentBinding.BUNDLE,
        ArgumentBinding.SPREAD_AUTHOR_WINS,
        ArgumentBinding.SPREAD_CALLER_WINS,
    ],
    ids=["bundle", "author-wins", "caller-wins"],
)
async def test_a_service_target_lookup_reads_the_judged_route_under_every_binding(
    binding: ArgumentBinding,
) -> None:
    looked_up: list[Any] = []

    def _lookup(project_pk: Any = None, **extras: Any) -> Any:
        looked_up.append(project_pk)
        return {"pk": project_pk}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="archive_project",
        description="Archive a project.",
        spec=ServiceSpec(
            service=_archive_target,
            input_serializer=_AliasInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        ),
        url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
        argument_binding=binding,
    )

    out = await _call(
        server, {"project_pk": 7, "project": 8}, is_async=True, name="archive_project"
    )

    assert out.get("isError") is not True, f"answered {out!r}"
    assert looked_up == [7]
