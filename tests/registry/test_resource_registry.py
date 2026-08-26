from __future__ import annotations

import pytest
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding


def _binding(uri_template: str, name: str = "r") -> ResourceBinding:
    return ResourceBinding(
        name=name,
        uri_template=uri_template,
        description=None,
        selector=lambda: None,
        kind=SelectorKind.LIST,
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


def test_a_concrete_uri_wins_over_a_template_registered_first() -> None:
    """Which binding serves a URI must not depend on registration order.

    A template's ``{var}`` matches any single segment, so ``reports://{id}``
    also matches ``reports://all-tenants-summary``. Resolving in registration
    order made *which permission stack guards a URI* a function of the order the
    two were registered in, and the wrong answer is the permissive one — the
    template is the general case, guarded for the general caller.
    """
    reg = ResourceRegistry()
    template = _binding("reports://{report_id}", name="report")
    concrete = _binding("reports://all-tenants-summary", name="summary")
    reg.register(template)
    reg.register(concrete)

    found = reg.resolve("reports://all-tenants-summary")
    assert found is not None
    matched, vars_ = found
    assert matched is concrete
    assert vars_ == {}


def test_the_template_still_serves_every_other_uri_under_it() -> None:
    reg = ResourceRegistry()
    template = _binding("reports://{report_id}", name="report")
    concrete = _binding("reports://all-tenants-summary", name="summary")
    reg.register(template)
    reg.register(concrete)

    found = reg.resolve("reports://42")
    assert found is not None
    matched, vars_ = found
    assert matched is template
    assert vars_ == {"report_id": "42"}


def test_registration_order_still_decides_between_two_overlapping_templates() -> None:
    reg = ResourceRegistry()
    first = _binding("reports://{report_id}", name="a")
    reg.register(first)
    reg.register(_binding("reports://{slug}", name="b"))
    found = reg.resolve("reports://42")
    assert found is not None
    assert found[0] is first
