from __future__ import annotations

from rest_framework_mcp.registry.types.prompt_binding import PromptBinding


class PromptRegistry:
    """Name to
    [`PromptBinding`][rest_framework_mcp.registry.types.prompt_binding.PromptBinding]
    lookup.

    Mirrors [`ToolRegistry`][rest_framework_mcp.registry.tool_registry.ToolRegistry]:
    names are unique and a duplicate raises at registration."""

    def __init__(self) -> None:
        self._bindings: dict[str, PromptBinding] = {}

    def register(self, binding: PromptBinding) -> None:
        if binding.name in self._bindings:
            raise ValueError(f"Duplicate MCP prompt name: {binding.name!r}")
        self._bindings[binding.name] = binding

    def get(self, name: str) -> PromptBinding | None:
        return self._bindings.get(name)

    def all(self) -> list[PromptBinding]:
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


__all__ = ["PromptRegistry"]
