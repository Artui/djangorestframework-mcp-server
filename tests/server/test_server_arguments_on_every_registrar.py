"""``server_arguments=`` means the same on every registrar.

The deprecation of ``agent_contract=`` points at ``server_arguments=``, so each
route a tool can be registered by takes it: ``register_service_tool`` /
``register_selector_tool``, the ``service_tool`` / ``selector_tool``
decorators, a ``register_specs`` override, and a ``ToolDefinition`` handed to
``register_tools``. Each route is asked both halves: a bound value reaches the
callable, so a route that accepted the keyword and dropped it fails, and a
binding that cannot hold is refused naming the tool, by the same check.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind, ServerArguments, SpecRegistry
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import SelectorSpec, ServiceSpec

from rest_framework_mcp import MCPServer, ToolDefinition, register_tools
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_ROUTES = ["direct", "decorator", "register_specs", "register_tools"]
_KINDS = ["selector", "service"]


class _Note(serializers.Serializer):
    project = serializers.CharField()
    note = serializers.CharField()


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _register(
    server: MCPServer, *, route: str, kind: str, bind: ServerArguments, seen: list[Any]
) -> None:
    """Register one tool named ``op`` of ``kind`` through ``route``, bound with ``bind``."""

    def card(*, project: str = "none") -> dict[str, Any]:
        seen.append(project)
        return {"project": project}

    def add_note(*, data: Any) -> dict[str, Any]:
        seen.append(dict(data))
        return {"ok": True}

    spec: Any
    if kind == "selector":
        spec = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=card, permission_classes=[AllowAny]
        )
    else:
        spec = ServiceSpec(
            service=add_note, input_serializer=_Note, atomic=False, permission_classes=[AllowAny]
        )
    if route == "direct":
        if kind == "selector":
            server.register_selector_tool(name="op", spec=spec, server_arguments=bind)
        else:
            server.register_service_tool(name="op", spec=spec, server_arguments=bind)
    elif route == "decorator":
        if kind == "selector":
            server.selector_tool(name="op", spec=spec, server_arguments=bind)(card)
        else:
            server.service_tool(name="op", spec=spec, server_arguments=bind)(add_note)
    elif route == "register_specs":
        registry = SpecRegistry()
        registry.register("op", spec)
        server.register_specs(registry, overrides={"op": {"server_arguments": bind}})
    else:
        factory = ToolDefinition.selector if kind == "selector" else ToolDefinition.service
        register_tools(server, [factory(name="op", spec=spec, server_arguments=bind)])


@pytest.mark.django_db
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
def test_a_bound_value_reaches_the_callable(route: str, kind: str) -> None:
    seen: list[Any] = []
    server = _server()
    _register(
        server, route=route, kind=kind, bind=ServerArguments(values={"project": "7"}), seen=seen
    )
    arguments = {} if kind == "selector" else {"note": "hi"}

    result = server.call_tool("op", arguments, user=None).to_dict()

    assert result.get("isError") is not True, result
    assert seen == (["7"] if kind == "selector" else [{"project": "7", "note": "hi"}])


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("route", _ROUTES)
def test_a_binding_that_cannot_hold_is_refused_naming_the_tool(route: str, kind: str) -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=r"'op'.*tenant"):
        _register(
            server, route=route, kind=kind, bind=ServerArguments(values={"tenant": 1}), seen=[]
        )

    assert "op" not in server.tools
