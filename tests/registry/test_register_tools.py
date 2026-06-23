"""End-to-end tests for :func:`register_tools` and the defaults dataclasses.

Verifies the bulk-registration loop produces bindings that are
field-equivalent to the equivalent imperative calls. The point of
``register_tools`` is *less boilerplate, identical wire shape* — these
tests pin the equivalence.
"""

from __future__ import annotations

import pytest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import (
    ArgumentBinding,
    OutputFormat,
    ToolKind,
    UnknownArguments,
)
from rest_framework_mcp.registry.register_tools import register_tools
from rest_framework_mcp.registry.types.selector_defaults import SelectorDefaults
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.service_defaults import ServiceDefaults
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.tool_definition import ToolDefinition
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _svc() -> None:
    return None


def _sel() -> list[dict[str, str]]:
    return []


# ---------- Basic shape ----------


def test_returns_bindings_in_definition_order() -> None:
    server = _server()
    sp1 = ServiceSpec(service=_svc, atomic=False)
    sp2 = SelectorSpec(kind=SelectorKind.LIST, selector=_sel)
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.service(name="a", spec=sp1),
            ToolDefinition.selector(name="b", spec=sp2),
        ],
    )
    assert [b.name for b in bindings] == ["a", "b"]
    assert isinstance(bindings[0], ToolBinding)
    assert isinstance(bindings[1], SelectorToolBinding)


def test_each_definition_registers_against_the_server_registry() -> None:
    server = _server()
    register_tools(
        server,
        definitions=[
            ToolDefinition.service(name="a", spec=ServiceSpec(service=_svc, atomic=False)),
            ToolDefinition.selector(
                name="b", spec=SelectorSpec(kind=SelectorKind.LIST, selector=_sel)
            ),
        ],
    )
    assert server.tools.get("a") is not None
    assert server.tools.get("b") is not None


# ---------- Defaults vs per-definition kwargs ----------


def test_service_defaults_apply_when_definition_unset() -> None:
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.service(name="a", spec=ServiceSpec(service=_svc, atomic=False)),
        ],
        service_defaults=ServiceDefaults(
            output_format=OutputFormat.TOON,
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        ),
    )
    binding = bindings[0]
    assert isinstance(binding, ToolBinding)
    assert binding.output_format is OutputFormat.TOON
    assert binding.argument_binding is ArgumentBinding.SPREAD_AUTHOR_WINS


def test_definition_wins_over_defaults_on_conflict() -> None:
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.service(
                name="a",
                spec=ServiceSpec(service=_svc, atomic=False),
                output_format=OutputFormat.JSON,
            ),
        ],
        service_defaults=ServiceDefaults(output_format=OutputFormat.TOON),
    )
    assert bindings[0].output_format is OutputFormat.JSON


def test_selector_defaults_apply_when_definition_unset() -> None:
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.selector(
                name="b",
                spec=SelectorSpec(kind=SelectorKind.LIST, selector=_sel),
            ),
        ],
        selector_defaults=SelectorDefaults(
            paginate=True,
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        ),
    )
    binding = bindings[0]
    assert isinstance(binding, SelectorToolBinding)
    assert binding.paginate is True
    assert binding.unknown_arguments is UnknownArguments.PASSTHROUGH


def test_service_defaults_do_not_leak_to_selector_definitions() -> None:
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.service(name="a", spec=ServiceSpec(service=_svc, atomic=False)),
            ToolDefinition.selector(
                name="b",
                spec=SelectorSpec(kind=SelectorKind.LIST, selector=_sel),
            ),
        ],
        service_defaults=ServiceDefaults(output_format=OutputFormat.TOON),
        selector_defaults=SelectorDefaults(output_format=OutputFormat.JSON),
    )
    # Defaults are kind-scoped — selector binding doesn't pick up service defaults.
    assert bindings[0].output_format is OutputFormat.TOON  # service
    assert bindings[1].output_format is OutputFormat.JSON  # selector


# ---------- Field-by-field parity with imperative ----------


def test_register_tools_matches_imperative_field_for_field() -> None:
    """Bulk-registered bindings are equal to the equivalent imperative call."""
    imperative_server = _server()
    imperative = imperative_server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, atomic=False),
        output_format=OutputFormat.TOON,
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments=UnknownArguments.IGNORE,
    )

    bulk_server = _server()
    [bulk] = register_tools(
        bulk_server,
        definitions=[
            ToolDefinition.service(
                name="t",
                spec=ServiceSpec(service=_svc, atomic=False),
                output_format=OutputFormat.TOON,
                argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
                unknown_arguments=UnknownArguments.IGNORE,
            ),
        ],
    )

    # Field-by-field equivalence (specs differ by identity since they're
    # constructed fresh; everything else should match).
    assert bulk.name == imperative.name
    assert bulk.output_format == imperative.output_format
    assert bulk.argument_binding == imperative.argument_binding
    assert bulk.unknown_arguments == imperative.unknown_arguments
    assert bulk.include_structured_content == imperative.include_structured_content
    assert bulk.include_output_schema == imperative.include_output_schema


# ---------- No defaults supplied ----------


def test_no_defaults_uses_registration_method_defaults() -> None:
    server = _server()
    bindings = register_tools(
        server,
        definitions=[
            ToolDefinition.service(name="a", spec=ServiceSpec(service=_svc, atomic=False)),
        ],
    )
    binding = bindings[0]
    assert isinstance(binding, ToolBinding)
    # Inherits ``register_service_tool``'s own defaults.
    assert binding.output_format is OutputFormat.JSON
    assert binding.argument_binding is ArgumentBinding.BUNDLE
    assert binding.unknown_arguments is UnknownArguments.REJECT


# ---------- ToolKind dispatch ----------


def test_tool_kind_is_internal_discriminator() -> None:
    """Direct construction with an unsupported ``ToolKind`` value still routes correctly."""
    server = _server()
    d = ToolDefinition(
        kind=ToolKind.SERVICE,
        name="z",
        spec=ServiceSpec(service=_svc, atomic=False),
    )
    [binding] = register_tools(server, definitions=[d])
    assert isinstance(binding, ToolBinding)


# ---------- Edge cases ----------


def test_empty_definitions_returns_empty_list() -> None:
    server = _server()
    bindings = register_tools(server, definitions=[])
    assert bindings == []


def test_iterator_input_consumed_in_order() -> None:
    """``definitions`` is an ``Iterable``, not a ``Sequence`` — generators work."""
    server = _server()

    def gen() -> object:
        yield ToolDefinition.service(name="a", spec=ServiceSpec(service=_svc, atomic=False))
        yield ToolDefinition.service(name="b", spec=ServiceSpec(service=_svc, atomic=False))

    bindings = register_tools(server, definitions=gen())  # type: ignore[arg-type]
    assert [b.name for b in bindings] == ["a", "b"]


# ---------- Defaults dataclasses ----------


def test_selector_defaults_default_constructor_has_no_overrides() -> None:
    """A bare ``SelectorDefaults()`` has every field ``None`` (no overrides)."""
    d = SelectorDefaults()
    assert d.paginate is None
    assert d.output_format is None
    assert d.unknown_arguments is None


def test_service_defaults_default_constructor_has_no_overrides() -> None:
    d = ServiceDefaults()
    assert d.output_format is None
    assert d.argument_binding is None
    assert d.unknown_arguments is None


def test_defaults_dataclasses_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    sd = ServiceDefaults(output_format=OutputFormat.TOON)
    with pytest.raises(FrozenInstanceError):
        sd.output_format = OutputFormat.JSON  # type: ignore[misc]
