from __future__ import annotations

import pytest
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.tool_binding import ToolBinding
from rest_framework_mcp.registry.tool_registry import ToolRegistry


def _binding(name: str = "t") -> ToolBinding:
    return ToolBinding(name=name, description=None, spec=ServiceSpec(service=lambda: None))


def test_register_and_lookup() -> None:
    reg = ToolRegistry()
    b = _binding("a")
    reg.register(b)
    assert reg.get("a") is b
    assert "a" in reg
    assert reg.get("missing") is None
    assert len(reg) == 1
    assert reg.all() == [b]


def test_register_duplicate_raises() -> None:
    reg = ToolRegistry()
    reg.register(_binding("a"))
    with pytest.raises(ValueError, match="Duplicate"):
        reg.register(_binding("a"))
