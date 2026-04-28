from __future__ import annotations

import pytest

from rest_framework_mcp.registry.resource_binding import ResourceBinding
from rest_framework_mcp.registry.resource_registry import ResourceRegistry


def _binding(uri_template: str, name: str = "r") -> ResourceBinding:
    return ResourceBinding(
        name=name, uri_template=uri_template, description=None, selector=lambda: None
    )


def test_register_and_resolve_concrete() -> None:
    reg = ResourceRegistry()
    binding = _binding("invoices://")
    reg.register(binding)
    found = reg.resolve("invoices://")
    assert found is not None
    matched, vars_ = found
    assert matched is binding
    assert vars_ == {}


def test_register_and_resolve_template() -> None:
    reg = ResourceRegistry()
    binding = _binding("invoices://{pk}")
    reg.register(binding)
    found = reg.resolve("invoices://42")
    assert found is not None
    _, vars_ = found
    assert vars_ == {"pk": "42"}


def test_resolve_unknown_returns_none() -> None:
    reg = ResourceRegistry()
    reg.register(_binding("invoices://"))
    assert reg.resolve("nope://x") is None


def test_register_duplicate_raises() -> None:
    reg = ResourceRegistry()
    reg.register(_binding("u://"))
    with pytest.raises(ValueError, match="Duplicate"):
        reg.register(_binding("u://"))


def test_concrete_and_templates_partition() -> None:
    reg = ResourceRegistry()
    a = _binding("u://", name="a")
    b = _binding("u://{x}", name="b")
    reg.register(a)
    reg.register(b)
    assert reg.concrete() == [a]
    assert reg.templates() == [b]
    assert len(reg) == 2
    assert reg.all() == [a, b]


def test_resource_binding_is_template_property() -> None:
    assert _binding("u://{x}").is_template is True
    assert _binding("u://").is_template is False
