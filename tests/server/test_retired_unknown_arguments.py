"""``UnknownArguments.PASSTHROUGH`` is refused when a tool is registered.

drf-services 0.58.0 retired the member: an argument the spec does not declare
never reaches a callable, so its ``dispatch_spec`` raises ``ValueError`` for it,
and a chain, which validates its arguments here, raises the same words. The
member stays importable, so a server could still register a tool carrying it
and fail every call. Each case is one public route that reaches the
registrars, because a route that skipped the refusal would put that tool back.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind, SpecRegistry, UnknownArguments
from rest_framework.permissions import IsAuthenticated
from rest_framework_services import SelectorSpec, ServiceSpec

from rest_framework_mcp import (
    ChainStep,
    SelectorDefaults,
    ServiceDefaults,
    ToolDefinition,
    register_tools,
)
from rest_framework_mcp.server.mcp_server import MCPServer

RETIRED = UnknownArguments.PASSTHROUGH
# The refusal names both supported policies and says why the member cannot come back.
MESSAGE = r"(?s)UnknownArguments.PASSTHROUGH is retired.*REJECT.*IGNORE"


def _service(**_: Any) -> dict[str, str]:
    return {"ok": "true"}


def _rows(**_: Any) -> list[dict[str, str]]:
    return []


def _service_spec() -> ServiceSpec:
    return ServiceSpec(service=_service, atomic=False, permission_classes=[IsAuthenticated])


def _selector_spec() -> SelectorSpec:
    return SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_rows, permission_classes=[IsAuthenticated]
    )


def test_register_service_tool_refuses_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE) as caught:
        server.register_service_tool(name="refund", spec=_service_spec(), unknown_arguments=RETIRED)

    assert "'refund'" in str(caught.value)
    assert "never reaches the callable" in str(caught.value)
    assert server.tools.get("refund") is None


def test_register_selector_tool_refuses_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE) as caught:
        server.register_selector_tool(
            name="lookup", spec=_selector_spec(), unknown_arguments=RETIRED
        )

    assert "'lookup'" in str(caught.value)
    assert server.tools.get("lookup") is None


def test_register_chain_tool_refuses_it() -> None:
    # A chain validates its arguments here and raises for the member on every
    # call; refused at registration, the tool is never listed to fail.
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE) as caught:
        server.register_chain_tool(
            name="flow",
            steps=[ChainStep(alias="issued", spec=_service_spec())],
            unknown_arguments=RETIRED,
        )

    assert "'flow'" in str(caught.value)
    assert server.tools.get("flow") is None


def test_the_service_decorator_refuses_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE):

        @server.service_tool(name="refund", unknown_arguments=RETIRED, atomic=False)
        def _refund(**_: Any) -> None: ...

    assert server.tools.get("refund") is None


def test_the_selector_decorator_refuses_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE):

        @server.selector_tool(name="lookup", kind=SelectorKind.RETRIEVE, unknown_arguments=RETIRED)
        def _lookup(**_: Any) -> None: ...

    assert server.tools.get("lookup") is None


@pytest.mark.parametrize(
    "spec_factory", [_service_spec, _selector_spec], ids=["service", "selector"]
)
def test_a_register_specs_override_refuses_it(spec_factory: Any) -> None:
    registry = SpecRegistry()
    registry.register("op", spec_factory())
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE):
        server.register_specs(registry, overrides={"op": {"unknown_arguments": RETIRED}})

    assert server.tools.get("op") is None


def test_a_tool_definition_refuses_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE):
        register_tools(
            server,
            definitions=[
                ToolDefinition.service(
                    name="refund", spec=_service_spec(), unknown_arguments=RETIRED
                )
            ],
        )

    assert server.tools.get("refund") is None


def test_service_defaults_refuse_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE):
        register_tools(
            server,
            definitions=[ToolDefinition.service(name="refund", spec=_service_spec())],
            service_defaults=ServiceDefaults(unknown_arguments=RETIRED),
        )

    assert server.tools.get("refund") is None


def test_selector_defaults_refuse_it() -> None:
    server = MCPServer(name="probe")

    with pytest.raises(ImproperlyConfigured, match=MESSAGE):
        register_tools(
            server,
            definitions=[ToolDefinition.selector(name="lookup", spec=_selector_spec())],
            selector_defaults=SelectorDefaults(unknown_arguments=RETIRED),
        )

    assert server.tools.get("lookup") is None


@pytest.mark.parametrize(
    "policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE], ids=lambda member: member.name
)
def test_every_other_member_still_registers(policy: UnknownArguments) -> None:
    server = MCPServer(name="probe")

    server.register_service_tool(name="refund", spec=_service_spec(), unknown_arguments=policy)
    server.register_selector_tool(name="lookup", spec=_selector_spec(), unknown_arguments=policy)
    server.register_chain_tool(
        name="flow",
        steps=[ChainStep(alias="issued", spec=_service_spec())],
        unknown_arguments=policy,
    )

    assert server.tools.get("refund") is not None
    assert server.tools.get("lookup") is not None
    assert server.tools.get("flow") is not None
