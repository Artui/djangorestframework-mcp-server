from __future__ import annotations

from rest_framework_mcp.registry.tool_binding import ToolBinding


class ToolRegistry:
    """Name → :class:`ToolBinding` lookup.

    Names must be unique. The registry rejects duplicates loudly so a
    misconfigured project surfaces the conflict at discovery time rather than
    silently shadowing a tool.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, ToolBinding] = {}

    def register(self, binding: ToolBinding) -> None:
        if binding.name in self._bindings:
            raise ValueError(f"Duplicate MCP tool name: {binding.name!r}")
        self._bindings[binding.name] = binding

    def get(self, name: str) -> ToolBinding | None:
        return self._bindings.get(name)

    def all(self) -> list[ToolBinding]:
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


__all__ = ["ToolRegistry"]
