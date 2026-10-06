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

A kwarg the call sent therefore reaches the selector as sent, uncoerced,
whatever the serializer validated under its name. For a kwarg the call left
out, a field declared under its name supplies only its default, which is the
author's and fills a name registration counted as filled; nothing else the
serializer validated under the name reaches the selector.

**Service tools**, as ``docs/concepts.md`` states beside the bindings. A URL
kwarg does not reach a service's pool at all, only ``view.kwargs``, so a
``spec.kwargs`` provider copying it into the pool is a provider like any other,
which the caller's value outranks under ``SPREAD_CALLER_WINS``. The target
lookup reads ``view.kwargs`` under every binding, so it is where a service that
must act on the judged route takes it from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework_dataclasses.serializers import DataclassSerializer
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
    input_serializer: type[Any] = _AliasInput,
    *,
    requires_project: bool = False,
) -> MCPServer:
    def _project(*, project_pk: Any = None, **extras: Any) -> dict[str, Any]:
        read.append(project_pk)
        return {"project_pk": project_pk}

    def _project_required(*, project_pk: Any, **extras: Any) -> dict[str, Any]:
        read.append(project_pk)
        return {"project_pk": project_pk}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_selector_tool(
        name="read_project",
        description="Read a project.",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_project_required if requires_project else _project,
            permission_classes=[permission],
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


def _recording(seen: list[dict[str, Any]]) -> type[BasePermission]:
    """Admits every route, recording the one judged."""

    class _Records(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return True

    return _Records


class _SameNameInput(serializers.Serializer):
    """A field declared under the URL kwarg's own name, coercing and defaulting it."""

    project_pk = serializers.IntegerField(default=5)


class _SameNameTrims(serializers.Serializer):
    """A ``CharField`` namesake, which trims whitespace by default."""

    project_pk = serializers.CharField()


class _SameNameThenValidateRewrites(serializers.Serializer):
    """A namesake whose value the serializer's ``validate`` replaces with ``project``'s."""

    project_pk = serializers.IntegerField(required=False)
    project = serializers.IntegerField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "project" in attrs:
            attrs["project_pk"] = attrs.pop("project")
        return attrs


class _AliasBesideNamesake(serializers.Serializer):
    """An alias declared after the namesake, so its value is the one validated.

    DRF writes the fields in declaration order, and the later write wins.
    """

    project_pk = serializers.IntegerField(required=False)
    project = serializers.IntegerField(source="project_pk", required=False)


class _Meta(serializers.Serializer):
    project_pk = serializers.IntegerField()


class _StarBesideNamesake(serializers.Serializer):
    """A ``source="*"`` field, which merges its own mapping into the top level."""

    project_pk = serializers.IntegerField(required=False)
    meta = _Meta(source="*", required=False)


class _ValidateRenames(serializers.Serializer):
    """No field writes ``project_pk``; the serializer's ``validate`` does."""

    project = serializers.IntegerField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "project" in attrs:
            attrs["project_pk"] = attrs.pop("project")
        return attrs


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@_SPREADING
@pytest.mark.parametrize(
    ("input_serializer", "url_kwarg_type", "arguments"),
    [
        (_SameNameInput, "integer", {"project_pk": "7"}),
        (_SameNameTrims, "string", {"project_pk": " acme "}),
        (_SameNameThenValidateRewrites, "integer", {"project_pk": 7, "project": 8}),
        (_AliasBesideNamesake, "integer", {"project_pk": 7, "project": 8}),
        (_StarBesideNamesake, "integer", {"project_pk": 7, "meta": {"project_pk": 8}}),
        (_ValidateRenames, "integer", {"project_pk": 7, "project": 8}),
    ],
    ids=[
        "namesake-coerces",
        "namesake-trims",
        "namesake-then-validate-rewrites",
        "alias-beside-namesake",
        "star-beside-namesake",
        "validate-without-namesake",
    ],
)
async def test_a_url_kwarg_the_call_sent_reaches_the_selector_as_sent(
    is_async: bool,
    binding: ArgumentBinding,
    input_serializer: type[serializers.Serializer],
    url_kwarg_type: str,
    arguments: dict[str, Any],
) -> None:
    # Each serializer validates ``project_pk`` to something other than what
    # was sent: a coercion, a trim, or 8 from another argument. Whatever wrote
    # it, the overlay's value is dropped, so the selector reads the value the
    # permission judged, uncoerced, rather than the overlay's under
    # ``SPREAD_CALLER_WINS``. A trimmed ``" acme "`` is another row to a
    # string lookup, and the namesake's own coercion is no exception to the
    # rule: the route reaches the selector as sent.
    seen: list[dict[str, Any]] = []
    read: list[Any] = []
    server = _server(
        binding,
        _recording(seen),
        read,
        UrlKwarg("project_pk", type=url_kwarg_type, required=True),
        input_serializer,
    )

    out = await _call(server, arguments, is_async=is_async)

    assert out.get("isError") is not True, f"answered {out!r}"
    sent = arguments["project_pk"]
    assert repr(read) == repr([sent])
    assert seen == [{"project_pk": sent}, {"project_pk": sent}]


class _DefaultBesideAlias(serializers.Serializer):
    """A namesake default beside an alias the caller can fill."""

    project_pk = serializers.IntegerField(default=5)
    project = serializers.IntegerField(source="project_pk", required=False)


class _DefaultThenValidateMoves(serializers.Serializer):
    """A namesake default, which ``validate`` overwrites from ``project``."""

    project_pk = serializers.IntegerField(default=5)
    project = serializers.IntegerField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "project" in attrs:
            attrs["project_pk"] = attrs.pop("project")
        return attrs


class _NullableDefault(serializers.Serializer):
    """A namesake default that also admits a null, which the split counts as left out."""

    project_pk = serializers.IntegerField(default=5, allow_null=True)


class _ReadOnlyDefault(serializers.Serializer):
    """A read-only namesake with a default, which DRF keeps out of the validated values."""

    project_pk = serializers.IntegerField(read_only=True, default=5)


@dataclass
class _ProjectInput:
    project_pk: int = 5


class _DataclassDefault(DataclassSerializer):
    """A dataclass input, which validates into an instance that is not overlaid.

    The field is declared rather than generated, so it carries a default of its
    own: a generated one leaves the default to the dataclass.
    """

    project_pk = serializers.IntegerField(default=5)

    class Meta:
        dataclass = _ProjectInput


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@_SPREADING
@pytest.mark.parametrize(
    ("input_serializer", "arguments", "requires_project", "expected"),
    [
        (_SameNameInput, {}, True, 5),
        (_NullableDefault, {"project_pk": None}, False, 5),
        (_DefaultBesideAlias, {"project": 8}, True, 5),
        (_DefaultThenValidateMoves, {"project": 8}, True, 5),
        (_SameNameThenValidateRewrites, {"project": 8}, False, None),
        (_ReadOnlyDefault, {}, False, None),
        (_DataclassDefault, {}, False, None),
    ],
    ids=[
        "namesake-default",
        "null-is-left-out",
        "default-beside-alias",
        "default-then-validate-moves",
        "no-default-then-validate-moves",
        "read-only-default",
        "dataclass-default",
    ],
)
async def test_a_url_kwarg_the_call_left_out_reaches_the_selector_only_as_a_namesake_default(
    is_async: bool,
    binding: ArgumentBinding,
    input_serializer: type[Any],
    arguments: dict[str, Any],
    requires_project: bool,
    expected: Any,
) -> None:
    # The route names no project, and that is what the permission judged. A
    # field declared under the kwarg's name with a default supplies it, so a
    # selector requiring the name runs, which registration promised by counting
    # the name as filled, rather than raising ``TypeError``. Nothing else
    # does: 8, moved onto ``project_pk`` by an alias or by ``validate``, is a
    # project nobody judged, so with no default the name stays out. A
    # read-only default never reaches the validated values, and a dataclass
    # input's values are not overlaid at all, so neither fills the name.
    seen: list[dict[str, Any]] = []
    read: list[Any] = []
    server = _server(
        binding,
        _recording(seen),
        read,
        UrlKwarg("project_pk", type="integer"),
        input_serializer,
        requires_project=requires_project,
    )

    out = await _call(server, arguments, is_async=is_async)

    assert out.get("isError") is not True, f"answered {out!r}"
    assert read == [expected]
    assert seen == [{}, {}]


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
