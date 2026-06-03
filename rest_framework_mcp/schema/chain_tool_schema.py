from __future__ import annotations

from typing import Any

from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema


def build_chain_tool_input_schema(binding: ChainToolBinding) -> dict[str, Any]:
    """Build the JSON Schema for a chain tool's ``inputSchema``.

    The chain advertises a single input shape: its explicit
    ``input_serializer`` when set, otherwise the first step's serializer
    (the first-step fallback — see
    :attr:`ChainToolBinding.resolved_input_serializer`). ``build_input_schema``
    handles ``None`` by returning an empty ``{"type": "object"}``.
    """
    return build_input_schema(binding.resolved_input_serializer)


__all__ = ["build_chain_tool_input_schema"]
