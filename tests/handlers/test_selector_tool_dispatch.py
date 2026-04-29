"""End-to-end selector-tool dispatch: filter → order → paginate → render.

Exercises ``handle_tools_call`` (sync) for selector-tool bindings,
covering each pipeline knob in isolation and combined. Async sibling
coverage lives in ``test_selector_tool_dispatch_async.py``.
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer

# ---------- Fixtures + helpers ----------


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()
    min_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="gte")

    class Meta:
        model = Invoice
        fields = ["sent"]


def _list_invoices(*, user: Any) -> Any:
    """Selector returns a raw, unscoped queryset — the tool layer narrows it."""
    return Invoice.objects.all()


# ---------- registration + tools/list ----------


def test_register_selector_tool_creates_binding() -> None:
    server = _server()
    binding = server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
    )
    assert binding.name == "invoices.list"
    assert server.tools.get("invoices.list") is binding


def test_register_selector_tool_rejects_spec_with_no_selector() -> None:
    server = _server()
    with pytest.raises(ValueError, match="selector=None"):
        server.register_selector_tool(
            name="x",
            spec=SelectorSpec(selector=None),
        )


def test_decorator_form_wraps_callable_in_spec() -> None:
    server = _server()

    @server.selector_tool(
        name="invoices.list",
        output_serializer=InvoiceOutputSerializer,
    )
    def list_invoices(*, user: Any) -> Any:  # noqa: ARG001
        return Invoice.objects.all()

    assert server.tools.get("invoices.list") is not None
    assert callable(list_invoices)  # original function returned unchanged


def test_tools_list_emits_filter_args_in_input_schema() -> None:
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        filter_set=InvoiceFilterSet,
        ordering_fields=["amount_cents"],
        paginate=True,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    properties = schema["properties"]
    # Filter properties merged in:
    assert properties["sent"] == {"type": "boolean"}
    assert properties["min_amount"] == {"type": "number"}
    # Ordering as enum of "<f>" / "-<f>":
    assert properties["ordering"]["enum"] == ["amount_cents", "-amount_cents"]
    # Pagination args:
    assert properties["page"] == {"type": "integer", "minimum": 1}
    assert properties["limit"] == {"type": "integer", "minimum": 1}


# ---------- Filtering ----------


@pytest.mark.django_db
def test_filter_narrows_queryset() -> None:
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)
    Invoice.objects.create(number="C", amount_cents=300, sent=True)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        filter_set=InvoiceFilterSet,
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {"sent": True}}, _ctx(server))
    assert isinstance(out, dict)
    items = out["structuredContent"]
    assert {item["number"] for item in items} == {"A", "C"}


@pytest.mark.django_db
def test_filter_with_no_args_returns_everything() -> None:
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        filter_set=InvoiceFilterSet,
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 2


@pytest.mark.django_db
def test_no_filter_set_means_no_filtering() -> None:
    """Selector tool without ``filter_set=`` returns the queryset verbatim."""
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
    )

    # Even when "sent" is in arguments, no filter applies.
    out = handle_tools_call({"name": "invoices.list", "arguments": {"sent": True}}, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 2


# ---------- Ordering ----------


@pytest.mark.django_db
def test_ordering_applies_when_value_is_allowed() -> None:
    Invoice.objects.create(number="C", amount_cents=300)
    Invoice.objects.create(number="A", amount_cents=100)
    Invoice.objects.create(number="B", amount_cents=200)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        ordering_fields=["amount_cents"],
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "amount_cents"}}, _ctx(server)
    )
    assert isinstance(out, dict)
    nums = [item["number"] for item in out["structuredContent"]]
    assert nums == ["A", "B", "C"]


@pytest.mark.django_db
def test_descending_ordering_is_supported() -> None:
    Invoice.objects.create(number="A", amount_cents=100)
    Invoice.objects.create(number="B", amount_cents=200)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        ordering_fields=["amount_cents"],
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "-amount_cents"}}, _ctx(server)
    )
    assert isinstance(out, dict)
    nums = [item["number"] for item in out["structuredContent"]]
    assert nums == ["B", "A"]


@pytest.mark.django_db
def test_ordering_with_disallowed_field_is_silently_ignored() -> None:
    """A client passing an unknown ordering field doesn't crash dispatch."""
    Invoice.objects.create(number="A", amount_cents=100)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        ordering_fields=["amount_cents"],
    )

    # ``number`` isn't in ordering_fields; the dispatch ignores it rather
    # than raising. A stricter validator could 400 instead, but silent
    # fall-through matches how the schema enum advertises only allowed
    # values to begin with.
    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "number"}}, _ctx(server)
    )
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 1


# ---------- Pagination ----------


@pytest.mark.django_db
def test_pagination_wraps_response_in_metadata() -> None:
    for i in range(7):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i * 100)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        ordering_fields=["amount_cents"],
        paginate=True,
    )

    out = handle_tools_call(
        {
            "name": "invoices.list",
            "arguments": {"ordering": "amount_cents", "page": 2, "limit": 3},
        },
        _ctx(server),
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["page"] == 2
    assert payload["totalPages"] == 3  # ceil(7/3)
    assert payload["hasNext"] is True
    assert [item["number"] for item in payload["items"]] == ["INV-3", "INV-4", "INV-5"]


@pytest.mark.django_db
def test_pagination_last_page_reports_no_next() -> None:
    for i in range(5):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        paginate=True,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": 1, "limit": 100}}, _ctx(server)
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["page"] == 1
    assert payload["totalPages"] == 1
    assert payload["hasNext"] is False
    assert len(payload["items"]) == 5


@pytest.mark.django_db
def test_pagination_defaults_to_page_one_limit_hundred() -> None:
    Invoice.objects.create(number="A", amount_cents=10)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        paginate=True,
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"]["page"] == 1


@pytest.mark.django_db
def test_pagination_clamps_invalid_inputs_to_safe_defaults() -> None:
    """Bool / non-coercible / negative values fall back to the defaults."""
    Invoice.objects.create(number="A", amount_cents=10)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        paginate=True,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": True, "limit": "lol"}},
        _ctx(server),
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["page"] == 1
    assert len(payload["items"]) == 1


@pytest.mark.django_db
def test_pagination_accepts_string_int() -> None:
    Invoice.objects.create(number="A", amount_cents=10)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        paginate=True,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": "1", "limit": "50"}},
        _ctx(server),
    )
    assert isinstance(out, dict)
    assert out["structuredContent"]["page"] == 1


# ---------- Filter + order + paginate combined ----------


@pytest.mark.django_db
def test_full_pipeline_filter_then_order_then_paginate() -> None:
    """The pipeline applies in order: filter → order → paginate."""
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)
    Invoice.objects.create(number="C", amount_cents=300, sent=True)
    Invoice.objects.create(number="D", amount_cents=400, sent=True)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices, output_serializer=InvoiceOutputSerializer),
        filter_set=InvoiceFilterSet,
        ordering_fields=["amount_cents"],
        paginate=True,
    )

    # Filter: sent=True → A, C, D. Order desc → D, C, A. Page 1 limit 2 → D, C.
    out = handle_tools_call(
        {
            "name": "invoices.list",
            "arguments": {
                "sent": True,
                "ordering": "-amount_cents",
                "page": 1,
                "limit": 2,
            },
        },
        _ctx(server),
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["totalPages"] == 2  # 3 items / limit 2
    assert payload["hasNext"] is True
    assert [item["number"] for item in payload["items"]] == ["D", "C"]


# ---------- input_serializer for non-filter args ----------


class _CustomArgs(drf_serializers.Serializer):
    expand = drf_serializers.BooleanField(required=False, default=False)


@pytest.mark.django_db
def test_selector_tool_with_input_serializer_validates_custom_args() -> None:
    """Non-filter custom args go through ``input_serializer``."""
    Invoice.objects.create(number="A", amount_cents=100)

    server = _server()

    seen_data: dict[str, Any] = {}

    def selector(*, data: dict[str, Any]) -> Any:
        seen_data.update(data)
        return Invoice.objects.all()

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=selector, output_serializer=InvoiceOutputSerializer),
        input_serializer=_CustomArgs,
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {"expand": True}}, _ctx(server))
    assert isinstance(out, dict)
    assert seen_data == {"expand": True}


@pytest.mark.django_db
def test_selector_tool_input_serializer_rejects_invalid_args() -> None:
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices),
        input_serializer=_CustomArgs,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"expand": "not-a-bool"}},
        _ctx(server),
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


# ---------- Selector returns non-queryset ----------


def test_selector_returning_list_skips_queryset_pipeline() -> None:
    """A selector returning a plain list bypasses filter/order/paginate (no QS shape)."""
    server = _server()

    def selector() -> list[dict[str, Any]]:
        return [{"number": "A"}, {"number": "B"}]

    server.register_selector_tool(
        name="things.list",
        spec=SelectorSpec(selector=selector),
        # filter_set is set but won't apply because the result isn't a QS.
        filter_set=InvoiceFilterSet,
    )

    out = handle_tools_call({"name": "things.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    # No output_serializer → list passes through.
    assert out["structuredContent"] == [{"number": "A"}, {"number": "B"}]


def test_selector_returning_none_renders_as_empty() -> None:
    """A scalar / None result gets rendered as-is."""
    server = _server()

    def selector() -> None:
        return None

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))

    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    # ``list(None)`` would crash; the path uses ``hasattr(__iter__)`` guard.
    # ``ToolResult.to_dict`` omits ``structuredContent`` when the payload
    # is ``None``, so the key isn't on the response.
    assert "structuredContent" not in out


# ---------- Auth / rate limit / errors ----------


class _DenyAll:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["scope:x"]


def test_selector_tool_denied_by_permission() -> None:
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices),
        permissions=[_DenyAll()],
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32002
    assert out.data == {"requiredScopes": ["scope:x"]}


def test_selector_tool_rate_limited() -> None:
    class _AlwaysDeny:
        def consume(self, request: HttpRequest, token: TokenInfo) -> int:
            return 42

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(selector=_list_invoices),
        rate_limits=[_AlwaysDeny()],
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32005
    assert out.data == {"retryAfter": 42}


def test_selector_tool_translates_service_validation_error() -> None:
    server = _server()

    def selector() -> None:
        raise ServiceValidationError({"f": ["bad"]})

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602
    assert out.data == {"detail": {"f": ["bad"]}}


def test_selector_tool_translates_service_error() -> None:
    server = _server()

    def selector() -> None:
        raise ServiceError("nope")

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32000


def test_selector_tool_records_service_error_when_setting_enabled(settings) -> None:
    """``RECORD_SERVICE_EXCEPTIONS=True`` exercises the otel ``record_exception`` branch."""
    settings.REST_FRAMEWORK_MCP = {"RECORD_SERVICE_EXCEPTIONS": True}
    server = _server()

    def selector() -> None:
        raise ServiceError("oh no")

    server.register_selector_tool(name="x", spec=SelectorSpec(selector=selector))
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32000


def test_selector_tool_kwargs_provider_merges_into_pool() -> None:
    """``SelectorSpec.kwargs`` callable feeds extra kwargs to the selector."""
    server = _server()

    seen: dict[str, Any] = {}

    def selector(*, scope: str) -> list[Any]:
        seen["scope"] = scope
        return []

    def kwargs_provider(view: Any, request: Any) -> dict[str, Any]:
        return {"scope": "from-provider"}

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(selector=selector, kwargs=kwargs_provider),
    )
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert seen == {"scope": "from-provider"}


def test_selector_tool_inputschema_with_required_input_serializer_field(settings) -> None:
    """An ``input_serializer`` with a required field surfaces in the merged schema."""

    class _Args(drf_serializers.Serializer):
        token = drf_serializers.CharField()  # required

    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(selector=_list_invoices),
        input_serializer=_Args,
        ordering_fields=["amount_cents"],
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    assert schema["required"] == ["token"]
    assert schema["properties"]["ordering"]["enum"] == ["amount_cents", "-amount_cents"]


def test_selector_tool_inputschema_minimal_no_optional_pipeline_knobs() -> None:
    """A binding with no filter / ordering / paginate / input_serializer."""
    server = _server()
    server.register_selector_tool(name="x", spec=SelectorSpec(selector=_list_invoices))
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    # Only the empty input-serializer-derived shape; no filter / ordering /
    # paginate properties added.
    assert schema == {"type": "object", "properties": {}}
