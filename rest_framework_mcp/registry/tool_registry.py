from __future__ import annotations

from rest_framework_mcp.registry.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.tool_binding import ToolBinding

# Either binding type counts as a "tool" on the wire — ``tools/list`` and
# ``tools/call`` discriminate at dispatch time. We deliberately do NOT
# define a shared base class so each binding stays a frozen dataclass with
# its own structure; the union here is enough for type checkers and runtime
# isinstance() checks.
ToolBindingLike = ToolBinding | SelectorToolBinding


class ToolRegistry:
    """Name → tool binding lookup.

    Holds both :class:`ToolBinding` (service tools, mutations) and
    :class:`SelectorToolBinding` (selector tools, reads). Names share a
    namespace — duplicates are rejected loudly so a misconfigured project
    surfaces the conflict at discovery time rather than silently shadowing
    a tool.
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
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


__all__ = ["ToolBindingLike", "ToolRegistry"]
