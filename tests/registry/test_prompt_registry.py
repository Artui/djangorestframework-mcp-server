from __future__ import annotations

import pytest

from rest_framework_mcp.registry.prompt_binding import PromptBinding
from rest_framework_mcp.registry.prompt_registry import PromptRegistry


def _binding(name: str = "p") -> PromptBinding:
    return PromptBinding(name=name, description=None, render=lambda **_: "x")


def test_register_and_lookup() -> None:
    reg = PromptRegistry()
    b = _binding("a")
    reg.register(b)
    assert reg.get("a") is b
    assert "a" in reg
    assert reg.get("missing") is None
    assert len(reg) == 1
    assert reg.all() == [b]


def test_register_duplicate_raises() -> None:
    reg = PromptRegistry()
    reg.register(_binding("a"))
    with pytest.raises(ValueError, match="Duplicate"):
        reg.register(_binding("a"))
