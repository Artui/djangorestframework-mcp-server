"""An ``input_serializer`` field named after a value dispatch fills itself is refused.

Under a spreading binding the validated fields are spread into the callable's
pool, where a seed of the same name (the kernel's ``RESERVED_POOL_SEEDS``, the
DRF edge's ``TOOL_EDGE_SEEDS``, a seed the server registers) keeps its own
value. The field registered, was advertised in the tool's ``inputSchema``, and
its value never reached a parameter of its name: a selector's ran on its
default, or on the seed for ``request`` / ``user`` / ``view``, and no call was
refused. So it is refused at registration, naming the tool, the field and the
reservation, on every registrar and both tool kinds.

What still registers, each case holding one condition of the guard
(``adapters.utils.validate_reserved_input_fields``): a service under ``BUNDLE``,
which receives the field inside ``data``; a read-only field and a
``HiddenField``, which take nothing from the caller; and a field bound with
``source=`` to another name, whose value reaches that name.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import DEFAULT_POOL_SEEDS, RESERVED_POOL_SEEDS, SelectorKind, SpecRegistry
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import ArgumentBinding, SelectorSpec, ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TOOL_EDGE_SEEDS
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.server.mcp_server import MCPServer

_RESERVED = sorted(RESERVED_POOL_SEEDS | TOOL_EDGE_SEEDS)
_ROUTES = ["direct", "decorator", "register_specs"]
_KINDS = ["selector", "service"]


def _server(**kwargs: Any) -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **kwargs)


def _input(name: str, **field_kwargs: Any) -> type[serializers.Serializer]:
    return type("Input", (serializers.Serializer,), {name: serializers.CharField(**field_kwargs)})


def _register(
    server: MCPServer,
    *,
    route: str,
    kind: str,
    input_serializer: type,
    argument_binding: ArgumentBinding = ArgumentBinding.SPREAD_AUTHOR_WINS,
    seen: dict[str, Any] | None = None,
    owner: bool = False,
) -> None:
    """Register one tool named ``op`` of ``kind`` through ``route``, recording its kwargs."""
    record = seen if seen is not None else {}

    def call(**kwargs: Any) -> list[Any]:
        record.update(kwargs)
        return []

    if owner:
        # A declared ``owner``: a bare catch-all receives no undeclared name.
        def call(*, owner: Any = None, **kwargs: Any) -> list[Any]:  # noqa: F811
            record.update(kwargs, owner=owner)
            return []

    if route == "decorator":
        if kind == "selector":
            server.selector_tool(
                name="op",
                kind=SelectorKind.LIST,
                input_serializer=input_serializer,
                argument_binding=argument_binding,
                permissions=[],
            )(call)
        else:
            server.service_tool(
                name="op",
                input_serializer=input_serializer,
                argument_binding=argument_binding,
                atomic=False,
                permissions=[],
            )(call)
        return
    spec: Any
    knobs: dict[str, Any] = {"argument_binding": argument_binding, "permissions": []}
    if kind == "selector":
        spec = SelectorSpec(kind=SelectorKind.LIST, selector=call, permission_classes=[AllowAny])
        knobs["input_serializer"] = input_serializer
    else:
        spec = ServiceSpec(
            service=call,
            input_serializer=input_serializer,
            atomic=False,
            permission_classes=[AllowAny],
        )
    if route == "direct":
        if kind == "selector":
            server.register_selector_tool(name="op", spec=spec, **knobs)
        else:
            server.register_service_tool(name="op", spec=spec, **knobs)
        return
    registry = SpecRegistry()
    registry.register("op", spec)
    server.register_specs(registry, overrides={"op": knobs})


def _call(server: MCPServer, arguments: dict[str, Any]) -> Any:
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    return handle_tools_call({"name": "op", "arguments": arguments}, ctx)


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("name", _RESERVED)
def test_a_field_named_after_a_seed_is_refused_at_registration(
    name: str, route: str, kind: str
) -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured) as caught:
        _register(server, route=route, kind=kind, input_serializer=_input(name, required=False))

    message = str(caught.value)
    assert "tool 'op'" in message
    assert f"input_serializer declares field(s) {[name]!r}" in message
    assert "dispatch fills itself" in message
    assert server.tools.get("op") is None


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("kind", _KINDS)
def test_a_dataclass_input_field_named_after_a_seed_is_refused(kind: str) -> None:
    @dataclasses.dataclass
    class Input:
        result: str = ""

    with pytest.raises(ImproperlyConfigured, match=r"declares field\(s\) \['result'\]"):
        _register(_server(), route="direct", kind=kind, input_serializer=Input)


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("kind", _KINDS)
def test_a_field_named_after_a_seed_the_server_registers_is_refused(kind: str) -> None:
    # A registered seed takes its name in every pool as the kernel's own do.
    server = _server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))

    with pytest.raises(ImproperlyConfigured, match=r"declares field\(s\) \['tenant'\]"):
        _register(server, route="direct", kind=kind, input_serializer=_input("tenant"))


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize(
    "binding",
    [ArgumentBinding.AUTO, ArgumentBinding.SPREAD_AUTHOR_WINS],
    ids=lambda member: member.name,
)
def test_a_selector_field_named_after_a_seed_is_refused_under_every_binding(
    binding: ArgumentBinding,
) -> None:
    # Every binding a selector registers with spreads; ``AUTO`` holds the
    # guard's selector condition, since for a service it bundles.
    with pytest.raises(ImproperlyConfigured, match=r"declares field\(s\) \['result'\]"):
        _register(
            _server(),
            route="direct",
            kind="selector",
            input_serializer=_input("result", required=False),
            argument_binding=binding,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "binding", [ArgumentBinding.BUNDLE, ArgumentBinding.AUTO], ids=lambda member: member.name
)
@pytest.mark.parametrize("name", _RESERVED)
def test_a_bundled_service_receives_the_field_inside_data(
    name: str, binding: ArgumentBinding
) -> None:
    # ``BUNDLE`` (and ``AUTO``, which bundles a service) spreads nothing, so the
    # field is the payload's and no seed meets it. Holds the guard's
    # spreading-binding condition.
    seen: dict[str, Any] = {}
    server = _server()
    _register(
        server,
        route="direct",
        kind="service",
        input_serializer=_input(name, required=False),
        argument_binding=binding,
        seen=seen,
    )

    _call(server, {name: "sent"})

    assert seen["data"] == {name: "sent"}


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize(
    "field",
    [
        serializers.CharField(read_only=True),
        serializers.HiddenField(default="server"),
    ],
    ids=["read-only", "hidden"],
)
def test_a_field_that_takes_nothing_from_the_caller_registers(kind: str, field: Any) -> None:
    # Neither field reads the arguments, so no caller's value is lost. A
    # ``HiddenField`` named ``user`` defaulting to the current user is the
    # common DRF shape this must leave alone.
    server = _server()
    input_serializer = type("Input", (serializers.Serializer,), {"user": field})

    _register(server, route="direct", kind=kind, input_serializer=input_serializer)

    assert server.tools.get("op") is not None


@pytest.mark.django_db
@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("kind", _KINDS)
def test_a_field_bound_to_another_name_reaches_that_name(kind: str) -> None:
    # ``source=`` puts the value under ``owner``, which no seed takes.
    seen: dict[str, Any] = {}
    server = _server()
    _register(
        server,
        route="direct",
        kind=kind,
        input_serializer=_input("user", source="owner"),
        seen=seen,
        owner=True,
    )

    _call(server, {"user": "sent"})

    assert seen["owner"] == "sent"
