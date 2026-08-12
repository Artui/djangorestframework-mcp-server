from __future__ import annotations

from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding

# Deliberately a union rather than a shared base class, so each binding stays a
# frozen dataclass with its own structure. ``tools/list`` and ``tools/call``
# discriminate at dispatch time.
ToolBindingLike = ToolBinding | SelectorToolBinding | ChainToolBinding


class ToolRegistry:
    """Name to tool binding lookup.

    Holds service, selector and chain bindings in one namespace, rejecting
    duplicates loudly so a misconfigured project surfaces the conflict at
    registration rather than silently shadowing a tool.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, ToolBindingLike] = {}

    def register(self, binding: ToolBindingLike) -> None:
        if binding.name in self._bindings:
            raise ValueError(f"Duplicate MCP tool name: {binding.name!r}")
        self._bindings[binding.name] = binding

    def get(self, name: str) -> ToolBindingLike | None:
        return self._bindings.get(name)

    def all(self) -> list[ToolBindingLike]:
        """Every binding, in **registration order**.

        ``tools/list`` has to be deterministic so clients can cache the catalog
        and cursor pagination stays stable, which dict insertion order already
        gives. Deliberately not sorted by name: registration order is authored
        order, which is a better first page for a model than an alphabetical
        one.
        """
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


__all__ = ["ToolBindingLike", "ToolRegistry"]
