"""Bulk-registration entry point for :class:`ToolDefinition` lists.

Sister of the imperative :meth:`MCPServer.register_service_tool` /
:meth:`MCPServer.register_selector_tool` and the decorator
``@server.service_tool`` / ``@server.selector_tool``. Additive surface —
``register_tools`` is a thin loop over the existing per-tool methods,
not a parallel registration engine, so every guarantee (and bug fix) of
the imperative API applies automatically.

Useful when a project has many tools in a single family and wants to
collapse the repetitive defaults into one place:

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


# Per-definition fields that ``register_tools`` strips before forwarding
# to the underlying registration method — these are positional kwargs
# (``name`` / ``spec``) or the discriminator (``kind``), not flexible
# kwargs that get merged with defaults.
_DEFINITION_FIXED_FIELDS: frozenset[str] = frozenset({"kind", "name", "spec"})


def register_tools(
    server: MCPServer,
    definitions: Iterable[ToolDefinition],
    *,
    selector_defaults: SelectorDefaults | None = None,
    service_defaults: ServiceDefaults | None = None,
) -> list[ToolBinding | SelectorToolBinding]:
    """Register every :class:`ToolDefinition` against ``server``.

    Defaults dataclasses supply per-kind kwarg defaults that are merged
    underneath each definition's own values (definition wins on
    conflict — every field on the definition that is *not* ``None`` is
    considered "set by the author").

    Returns the list of resulting bindings in the same order as
    ``definitions``, so test harnesses and observability code can
    introspect what landed.

    Raises :class:`TypeError` if a definition's ``kind`` is unrecognised
    (the discriminator is internal, so this can only happen via direct
    :class:`ToolDefinition` construction with an unsupported value).
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
    """Return the dataclass's non-``None`` fields as a dict.

    ``None`` is the "no override" sentinel across the
    :class:`ToolDefinition` / :class:`SelectorDefaults` /
    :class:`ServiceDefaults` family — stripping it here means the
    downstream ``register_*_tool`` method's own default takes effect
    rather than receiving an explicit ``None`` that might mean
    something different (e.g. ``include_structured_content=None`` is
    tri-state on the method).

    Accepts ``None`` itself for callers that didn't supply a defaults
    dataclass.
    """
    if obj is None:
        return {}
    return {
        f.name: getattr(obj, f.name)
        for f in dataclasses.fields(obj)
        if getattr(obj, f.name) is not None
    }


__all__ = ["register_tools"]
