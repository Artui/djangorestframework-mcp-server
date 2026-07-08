"""``MCPServer.call_tool`` — the transport-neutral, spec-backed invocation surface.

Drives service / selector tools through the sister repo's ``dispatch_spec`` core
off the HTTP path and returns a :class:`ToolResult`, mapping business failures
and missing instances to ``isError`` results while letting permission / input
faults propagate.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.protocol.types.tool_result import ToolResult


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _error_type(result: ToolResult) -> str:
    return json.loads(result.content[0].text)["error"]["type"]


# ---------- success paths ----------


def test_service_tool_returns_a_tool_result() -> None:
    server = _server()
    server.register_service_tool(
        name="things.create",
        spec=ServiceSpec(service=lambda **_: {"ok": True}, atomic=False),
    )
    result = server.call_tool("things.create", {"x": 1}, user=None)
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.structured_content == {"ok": True}


def test_selector_list_renders_many() -> None:
    server = _server()
    server.register_selector_tool(
        name="things.list",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: [{"a": 1}, {"a": 2}]),
    )
    result = server.call_tool("things.list", user=None)
    assert result.structured_content == [{"a": 1}, {"a": 2}]


def test_selector_retrieve_renders_single_instance() -> None:
    server = _server()
    server.register_selector_tool(
        name="things.get",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: {"a": 1}),
    )
    result = server.call_tool("things.get", {"pk": 1}, user=None)
    assert result.structured_content == {"a": 1}


def test_allow_none_retrieve_returns_null_not_an_error() -> None:
    server = _server()
    server.register_selector_tool(
        name="things.maybe",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None, allow_none=True),
    )
    result = server.call_tool("things.maybe", {"pk": 9}, user=None)
    assert result.is_error is False
    assert result.structured_content is None


def test_include_structured_content_false_omits_the_structured_field() -> None:
    server = _server()
    server.register_service_tool(
        name="things.quiet",
        spec=ServiceSpec(service=lambda **_: {"ok": True}, atomic=False),
        include_structured_content=False,
        # The MCP spec forbids advertising outputSchema while omitting
        # structuredContent, so drop the schema too.
        include_output_schema=False,
    )
    result = server.call_tool("things.quiet", user=None)
    assert result.structured_content is None
    assert result.content  # the text projection still carries the payload


# ---------- tool-level error paths (isError results) ----------


def test_missing_required_instance_is_a_not_found_error() -> None:
    server = _server()
    server.register_selector_tool(
        name="things.get",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda **_: None),
    )
    result = server.call_tool("things.get", {"pk": 404}, user=None)
    assert result.is_error is True
    assert _error_type(result) == "not_found"


def test_service_validation_error_becomes_a_tool_error() -> None:
    def svc(**_: Any) -> Any:
        raise ServiceValidationError("bad input")

    server = _server()
    server.register_service_tool(
        name="things.create",
        spec=ServiceSpec(service=svc, atomic=False),
    )
    result = server.call_tool("things.create", {"x": 1}, user=None)
    assert result.is_error is True
    assert _error_type(result) == "validation_error"


def test_service_error_becomes_a_tool_error() -> None:
    def svc(**_: Any) -> Any:
        raise ServiceError("boom")

    server = _server()
    server.register_service_tool(
        name="things.create",
        spec=ServiceSpec(service=svc, atomic=False),
    )
    result = server.call_tool("things.create", user=None)
    assert result.is_error is True
    assert _error_type(result) == "service_error"


# ---------- protocol faults (propagate) ----------


class _DenyAll(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return False


def test_spec_permission_denial_propagates() -> None:
    server = _server()
    server.register_service_tool(
        name="things.create",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False, permission_classes=[_DenyAll]),
    )
    with pytest.raises(PermissionDenied):
        server.call_tool("things.create", user=None)


def test_selector_spec_permission_denial_propagates() -> None:
    # Regression: a selector spec's class-level ``permission_classes``
    # used to leak through this spec-core surface — the ``on_target_resolved``
    # hook never fired on selector reads, and ``dispatch_spec`` never consults
    # ``permission_classes`` itself. The upfront ``enforce_permissions`` call now
    # denies before the selector runs.
    ran = False

    def _selector(**_: Any) -> Any:
        nonlocal ran
        ran = True
        return {"leaked": True}

    server = _server()
    server.register_selector_tool(
        name="things.secret",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_selector, permission_classes=[_DenyAll]
        ),
    )
    with pytest.raises(PermissionDenied):
        server.call_tool("things.secret", {"pk": 1}, user=None)
    assert ran is False  # denied before the selector could produce a payload


@pytest.mark.django_db
def test_selector_object_permission_denial_propagates() -> None:
    # Object-level half (rides drf-services >= 0.21): the
    # ``on_target_resolved=enforce_permissions`` hook now fires on selector
    # RETRIEVE dispatch, so an object-level denial on the *resolved row*
    # propagates through the spec-core surface — not just class-level denials.
    from tests.testapp.models import Invoice

    invoice = Invoice.objects.create(number="A", amount_cents=1)

    def _by_pk(*, pk: int) -> Any:
        return Invoice.objects.filter(pk=pk)

    server = _server()
    server.register_selector_tool(
        name="things.secret",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_by_pk, permission_classes=[_DenyObject]
        ),
    )
    with pytest.raises(PermissionDenied):
        server.call_tool("things.secret", {"pk": invoice.pk}, user=None)


def test_unknown_tool_name_raises_keyerror() -> None:
    server = _server()
    with pytest.raises(KeyError, match="nope"):
        server.call_tool("nope", user=None)


def test_chain_tool_is_unsupported() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[ChainStep("made", ServiceSpec(service=lambda **_: {}, atomic=False))],
    )
    with pytest.raises(TypeError, match="chain"):
        server.call_tool("chain", user=None)
