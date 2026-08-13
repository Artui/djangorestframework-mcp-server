"""Bulk-registration entry point for [`ToolDefinition`][rest_framework_mcp.registry.types.tool_definition.ToolDefinition] lists.

A thin loop over [`MCPServer.register_service_tool`][rest_framework_mcp.server.mcp_server.MCPServer.register_service_tool] /
[`MCPServer.register_selector_tool`][rest_framework_mcp.server.mcp_server.MCPServer.register_selector_tool] rather than a parallel registration
engine, so every guarantee of the imperative API applies unchanged. Useful when
a project has many tools in one family and wants the repetitive defaults in one
place:

.. code-block:: python

    register_tools(
        server,
        definitions=[
            ToolDefinition.selector(name="invoices.list", spec=ListInvoicesSpec),
            ToolDefinition.selector(name="invoices.retrieve", spec=GetInvoiceSpec),
        ],
        selector_defaults=SelectorDefaults(
            output_format=OutputFormat.TOON,
            paginate=True,
        ),
    )
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from rest_framework_mcp.constants import ToolKind
from rest_framework_mcp.registry.types.selector_defaults import SelectorDefaults
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.service_defaults import ServiceDefaults
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.tool_definition import ToolDefinition

if TYPE_CHECKING:  # pragma: no cover - imported only for typing
    from rest_framework_mcp.server.mcp_server import MCPServer


# Stripped before forwarding: these are the registration method's own
# positional kwargs, or the discriminator, not values merged with defaults.
_DEFINITION_FIXED_FIELDS: frozenset[str] = frozenset({"kind", "name", "spec"})


def register_tools(
    server: MCPServer,
    definitions: Iterable[ToolDefinition],
    *,
    selector_defaults: SelectorDefaults | None = None,
    service_defaults: ServiceDefaults | None = None,
) -> list[ToolBinding | SelectorToolBinding]:
    """Register every [`ToolDefinition`][rest_framework_mcp.registry.types.tool_definition.ToolDefinition] against ``server``.

    Args:
        server: The server to register against.
        definitions: The definitions to register, in order.
        selector_defaults: Per-kind defaults merged underneath each selector
            definition's own values. Any field the definition sets to something
            other than ``None`` counts as authored and wins.
        service_defaults: The same, for service definitions.

    Returns:
        The resulting bindings, in the order of ``definitions``, so test
        harnesses and observability code can introspect what landed.

    Raises:
        TypeError: The definition's ``kind`` is unrecognised. The discriminator
            is internal, so this needs direct [`ToolDefinition`][rest_framework_mcp.registry.types.tool_definition.ToolDefinition]
            construction with an unsupported value.
    """
    selector_defaults_kwargs: dict[str, Any] = _non_none_field_dict(selector_defaults)
    service_defaults_kwargs: dict[str, Any] = _non_none_field_dict(service_defaults)

    bindings: list[ToolBinding | SelectorToolBinding] = []
    for definition in definitions:
        per_def: dict[str, Any] = _non_none_field_dict(definition)
        for fixed in _DEFINITION_FIXED_FIELDS:
            per_def.pop(fixed, None)

        if definition.kind is ToolKind.SERVICE:
            kwargs: dict[str, Any] = {**service_defaults_kwargs, **per_def}
            bindings.append(
                server.register_service_tool(name=definition.name, spec=definition.spec, **kwargs)  # ty: ignore[invalid-argument-type]
            )
        elif definition.kind is ToolKind.SELECTOR:
            kwargs = {**selector_defaults_kwargs, **per_def}
            bindings.append(
                server.register_selector_tool(name=definition.name, spec=definition.spec, **kwargs)  # ty: ignore[invalid-argument-type]
            )
        else:  # pragma: no cover - exhaustive over ToolKind
            raise TypeError(f"Unrecognised ToolKind: {definition.kind!r}")

    return bindings


def _non_none_field_dict(obj: Any) -> dict[str, Any]:
    """Return the dataclass's non-``None`` fields as a dict, or ``{}`` for ``None``.

    ``None`` is the "no override" sentinel across the [`ToolDefinition`][rest_framework_mcp.registry.types.tool_definition.ToolDefinition] /
    [`SelectorDefaults`][rest_framework_mcp.registry.types.selector_defaults.SelectorDefaults] / [`ServiceDefaults`][rest_framework_mcp.registry.types.service_defaults.ServiceDefaults] family, and stripping
    it lets the downstream ``register_*_tool`` default apply — passing the
    ``None`` through would mean something else, since several of those kwargs
    are tri-state on the method.
    """
    if obj is None:
        return {}
    return {
        f.name: getattr(obj, f.name)
        for f in dataclasses.fields(obj)
        if getattr(obj, f.name) is not None
    }


__all__ = ["register_tools"]
