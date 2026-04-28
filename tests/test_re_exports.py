"""Confirm sister-repo Protocols + spec types are re-exported at top level."""

from __future__ import annotations


def test_service_protocols_reexported() -> None:
    from rest_framework_mcp import (
        CreateService,
        DeleteService,
        StrictCreateService,
        StrictDeleteService,
        StrictUpdateService,
        UpdateService,
    )

    for proto in (
        CreateService,
        UpdateService,
        DeleteService,
        StrictCreateService,
        StrictUpdateService,
        StrictDeleteService,
    ):
        assert proto.__module__.startswith("rest_framework_services.")


def test_selector_protocols_reexported() -> None:
    from rest_framework_mcp import (
        ListSelector,
        OutputSelector,
        RetrieveSelector,
        StrictListSelector,
        StrictOutputSelector,
        StrictRetrieveSelector,
    )

    for proto in (
        ListSelector,
        RetrieveSelector,
        OutputSelector,
        StrictListSelector,
        StrictRetrieveSelector,
        StrictOutputSelector,
    ):
        assert proto.__module__.startswith("rest_framework_services.")


def test_spec_and_view_reexported() -> None:
    from rest_framework_mcp import SelectorSpec, ServiceSpec, ServiceView

    assert ServiceSpec.__module__ == "rest_framework_services.types.service_spec"
    assert SelectorSpec.__module__ == "rest_framework_services.types.selector_spec"
    assert ServiceView.__module__ == "rest_framework_services.types.service_view"


def test_mcp_service_view_satisfies_protocol() -> None:
    """The MCP adapter quacks like a ``ServiceView``."""
    from django.http import HttpRequest

    from rest_framework_mcp import MCPServiceView, ServiceView
    from rest_framework_mcp.handlers.utils import build_internal_drf_request

    drf_request = build_internal_drf_request(HttpRequest(), user=None, data=None)
    view = MCPServiceView(request=drf_request, action="x", kwargs={"pk": 7})
    # Protocol attributes:
    assert view.request is drf_request
    assert view.kwargs == {"pk": 7}
    assert view.action == "x"
    # Structural conformance — ``runtime_checkable`` is not declared on
    # ``ServiceView`` upstream, so we assert the attribute set instead.
    assert {"request", "kwargs", "action"} <= set(dir(view))
    assert ServiceView is not None  # imported successfully
