"""Confirm sister-repo Protocols + spec types are re-exported at top level."""

from __future__ import annotations


def test_service_protocols_reexported() -> None:
    from rest_framework_mcp import CreateService, DeleteService, UpdateService

    for proto in (CreateService, UpdateService, DeleteService):
        assert proto.__module__.startswith("rest_framework_services.")


def test_selector_protocols_reexported() -> None:
    from rest_framework_mcp import ListSelector, RetrieveSelector

    for proto in (ListSelector, RetrieveSelector):
        assert proto.__module__.startswith("rest_framework_services.")


def test_selector_kind_reexported() -> None:
    from rest_framework_mcp import SelectorKind

    assert SelectorKind.__module__.startswith("rest_framework_services.")


def test_spec_and_view_reexported() -> None:
    from rest_framework_mcp import SelectorSpec, ServiceSpec, ServiceView

    assert ServiceSpec.__module__ == "rest_framework_services.types.service_spec"
    assert SelectorSpec.__module__ == "rest_framework_services.types.selector_spec"
    assert ServiceView.__module__ == "rest_framework_services.types.service_view"
