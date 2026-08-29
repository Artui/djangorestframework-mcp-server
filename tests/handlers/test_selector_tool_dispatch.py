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
from rest_framework import permissions as drf_permissions
from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer
from tests.utils import tool_error

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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
    )
    assert binding.name == "invoices.list"
    assert server.tools.get("invoices.list") is binding


def test_register_selector_tool_rejects_spec_with_no_selector() -> None:
    server = _server()
    with pytest.raises(ValueError, match="selector=None"):
        server.register_selector_tool(
            name="x",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=None),
        )


def test_decorator_form_wraps_callable_in_spec() -> None:
    server = _server()

    @server.selector_tool(
        name="invoices.list",
        kind=SelectorKind.LIST,
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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=InvoiceFilterSet,
        ),
        paginate=True,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    properties = schema["properties"]
    # Filter properties merged in:
    assert properties["sent"] == {"type": "boolean"}
    # ``min_amount`` is declared ``field_name="amount_cents", lookup_expr="gte"``,
    # so its own name gives away neither the column nor the comparison. Reflection
    # states both, because a model reading only the argument name would otherwise
    # have to guess whether "min" means ``gt`` or ``gte`` and against which field.
    # ``sent`` above says nothing extra: name, field and lookup already agree.
    assert properties["min_amount"] == {
        "type": "number",
        "description": "Matches `amount_cents` with the `gte` lookup.",
    }
    # Ordering is absent because this FilterSet declares no ``OrderingFilter``:
    # there is no other channel for it. The labelled ``oneOf`` one produces is
    # covered in ``test_selector_tool_ordering.py``.
    assert "ordering" not in properties
    # Pagination args:
    assert properties["page"] == {"type": "integer", "minimum": 1}
    # ``maximum`` mirrors the server's MAX_PAGE_SIZE, so the model sees the
    # ceiling dispatch will clamp to rather than discovering it by surprise.
    assert properties["limit"] == {"type": "integer", "minimum": 1, "maximum": 100}


# ---------- Filtering ----------


@pytest.mark.django_db
def test_filter_narrows_queryset() -> None:
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)
    Invoice.objects.create(number="C", amount_cents=300, sent=True)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=InvoiceFilterSet,
        ),
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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=InvoiceFilterSet,
        ),
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 2


@pytest.mark.django_db
def test_no_filter_set_means_no_filtering() -> None:
    """Selector tool without ``spec.filter_set`` returns the queryset verbatim."""
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
    )

    # Even when "sent" is in arguments, no filter applies.
    out = handle_tools_call({"name": "invoices.list", "arguments": {"sent": True}}, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 2


@pytest.mark.django_db
def test_retrieve_applies_filter_set_before_first() -> None:
    """A RETRIEVE selector shapes + filters its queryset before ``.first()``."""
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)

    server = _server()
    server.register_selector_tool(
        name="invoices.get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda **_: Invoice.objects.all(),
            output_serializer=InvoiceOutputSerializer,
            filter_set=InvoiceFilterSet,
        ),
    )
    # Filtering to sent=False selects B, not the first row (A).
    out = handle_tools_call({"name": "invoices.get", "arguments": {"sent": False}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "B"


# ---------- Pagination ----------


@pytest.mark.django_db
def test_pagination_wraps_response_in_metadata() -> None:
    for i in range(7):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i * 100)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        paginate=True,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": 2, "limit": 3}},
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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
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
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        paginate=True,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": "1", "limit": "50"}},
        _ctx(server),
    )
    assert isinstance(out, dict)
    assert out["structuredContent"]["page"] == 1


@pytest.mark.django_db
def test_pagination_is_shaped_upstream_from_already_parsed_ints(monkeypatch) -> None:
    """The page arithmetic is drf-services', and only the parsing is ours.

    Asserted as a handover rather than only as an outcome. The two
    implementations agreed exactly — 52 comparisons, no differences — on the day
    this transport's copy was deleted, so an outcome test alone would keep
    passing if a private copy grew back here, right up until drf-services moved
    a clamp and the two silently disagreed again. ``monkeypatch.setattr`` fails
    outright if the module stops reaching for the upstream shaper at all.

    The string arguments are the other half of the contract: ``_coerce_int``
    stays on this side because answering a malformed argument is a transport's
    policy, so what crosses the boundary is ``3`` and ``2``, never ``"3"``.
    """
    from rest_framework_mcp.handlers import selector_tool_dispatch

    for number in ("A", "B", "C", "D", "E"):
        Invoice.objects.create(number=number, amount_cents=10)

    handovers: list[dict[str, Any]] = []
    upstream = selector_tool_dispatch.paginate_output

    def _spy(rows: Any, **kwargs: Any) -> Any:
        handovers.append(kwargs)
        return upstream(rows, **kwargs)

    monkeypatch.setattr(selector_tool_dispatch, "paginate_output", _spy)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        paginate=True,
        max_page_size=4,
    )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": "3", "limit": "2"}},
        _ctx(server),
    )

    assert handovers == [{"page": 3, "limit": 2, "max_page_size": 4}]
    # And the envelope around the rendered rows is the one the page built, not a
    # second copy of the same ceil-divide living at this call site.
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert [row["number"] for row in payload["items"]] == ["E"]
    assert (payload["page"], payload["totalPages"], payload["hasNext"]) == (3, 3, False)


# ---------- Filter + paginate combined ----------


@pytest.mark.django_db
def test_full_pipeline_filter_then_paginate() -> None:
    """The pipeline applies in order: filter → paginate.

    Ordering would sit between the two, declared on the ``FilterSet`` and
    applied with the rest of the filtering — see ``test_selector_tool_ordering``
    for the three-channel composition.
    """
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)
    Invoice.objects.create(number="C", amount_cents=300, sent=True)
    Invoice.objects.create(number="D", amount_cents=400, sent=True)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=InvoiceFilterSet,
        ),
        paginate=True,
    )

    # Filter: sent=True → A, C, D. Page 1 limit 2 → two of the three.
    out = handle_tools_call(
        {
            "name": "invoices.list",
            "arguments": {"sent": True, "page": 1, "limit": 2},
        },
        _ctx(server),
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["totalPages"] == 2  # 3 items / limit 2
    assert payload["hasNext"] is True
    # The excluded row is the point: ``B`` is filtered out before the slice, so
    # a full first page still carries only rows the filter kept.
    assert set(payload["items"][0]) and "B" not in {item["number"] for item in payload["items"]}
    assert len(payload["items"]) == 2


# ---------- input_serializer for non-filter args ----------


class _CustomArgs(drf_serializers.Serializer):
    expand = drf_serializers.BooleanField(required=False, default=False)


@pytest.mark.django_db
def test_selector_tool_with_input_serializer_validates_custom_args() -> None:
    """Validated custom args spread to the selector's declared params (coerced)."""
    Invoice.objects.create(number="A", amount_cents=100)

    server = _server()

    seen: dict[str, Any] = {}

    def selector(*, expand: bool = False) -> Any:
        seen["expand"] = expand
        return Invoice.objects.all()

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=selector, output_serializer=InvoiceOutputSerializer
        ),
        input_serializer=_CustomArgs,
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {"expand": True}}, _ctx(server))
    assert isinstance(out, dict)
    # ``_CustomArgs`` coerces ``expand`` to a bool and it spreads to the selector.
    assert seen == {"expand": True}


@pytest.mark.django_db
def test_selector_tool_input_serializer_rejects_invalid_args() -> None:
    server = _server()

    def selector(*, user: Any, expand: bool = False) -> Any:  # noqa: ARG001
        return Invoice.objects.all()

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
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
        # A list-returning selector declares no queryset shaping (shaping +
        # filter_set require a QuerySet; see test_spec_shaping_and_context).
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
    )

    out = handle_tools_call({"name": "things.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    # No output_serializer → list passes through (pagination no-op).
    assert out["structuredContent"] == [{"number": "A"}, {"number": "B"}]


def test_selector_returning_none_renders_as_empty() -> None:
    """A scalar / None result gets rendered as-is."""
    server = _server()

    def selector() -> None:
        return None

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
    )

    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    # ``list(None)`` would crash; the path uses ``hasattr(__iter__)`` guard.
    # The tool does emit structured content, and its answer is null, so the key
    # is present and null — omitting it would be indistinguishable from a tool
    # that offers no structured channel at all.
    assert out["structuredContent"] is None


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
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list_invoices),
        permissions=[_DenyAll()],
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == -32006
    assert out.data == {"requiredScopes": ["scope:x"]}


def test_selector_tool_rate_limited() -> None:
    class _AlwaysDeny:
        def consume(self, request: HttpRequest, token: TokenInfo) -> int:
            return 42

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list_invoices),
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

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
    )
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["detail"] == {"f": ["bad"]}


def test_selector_tool_translates_service_error() -> None:
    server = _server()

    def selector() -> None:
        raise ServiceError("nope")

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
    )
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    error = tool_error(out)
    assert error["type"] == "service_error"
    assert error["message"] == "nope"


def test_selector_tool_records_service_error_when_setting_enabled(settings) -> None:
    """``RECORD_SERVICE_EXCEPTIONS=True`` exercises the otel ``record_exception`` branch."""
    settings.REST_FRAMEWORK_MCP = {
        "REQUIRE_TOOL_PERMISSIONS": False,
        "RECORD_SERVICE_EXCEPTIONS": True,
    }
    server = _server()

    def selector() -> None:
        raise ServiceError("oh no")

    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
    )
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert tool_error(out)["type"] == "service_error"


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
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector, kwargs=kwargs_provider),
    )
    out = handle_tools_call({"name": "x", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert seen == {"scope": "from-provider"}


def test_selector_tool_inputschema_with_required_input_serializer_field(settings) -> None:
    """An ``input_serializer`` with a required field surfaces in the merged schema."""

    class _Args(drf_serializers.Serializer):
        token = drf_serializers.CharField()  # required

    def selector(*, user: Any, token: str) -> Any:  # noqa: ARG001
        return Invoice.objects.all()

    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
        input_serializer=_Args,
        paginate=True,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    assert schema["required"] == ["token"]
    # The pipeline knob's own properties merge in alongside, unrequired.
    assert schema["properties"]["page"] == {"type": "integer", "minimum": 1}


def test_selector_tool_inputschema_minimal_no_optional_pipeline_knobs() -> None:
    """A binding with no filter / ordering / paginate / input_serializer."""
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list_invoices),
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    # Only the empty input-serializer-derived shape; no filter / ordering /
    # paginate properties added. ``additionalProperties`` is ``true`` even under
    # the default ``REJECT`` policy: with no ``input_serializer`` the runtime
    # can't reject unknown keys, so the schema stays open to match.
    assert schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


@pytest.mark.django_db
def test_selector_tool_honors_include_structured_content_override() -> None:
    """The per-binding override threads through the selector dispatch path."""
    Invoice.objects.create(number="A", amount_cents=100, sent=True)
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        include_structured_content=False,
        include_output_schema=False,
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert "structuredContent" not in out
    # The text payload still carries the full result.
    assert "A" in out["content"][0]["text"]


# ---------- kind=RETRIEVE — single-instance selector tools ----------


@pytest.mark.django_db
def test_retrieve_kind_renders_single_instance_with_serializer() -> None:
    """A retrieve selector tool calls ``output_serializer(many=False)``."""
    invoice = Invoice.objects.create(number="A", amount_cents=100, sent=True)

    def _get_invoice(*, user: Any) -> Any:  # noqa: ARG001
        return invoice

    server = _server()
    server.register_selector_tool(
        name="invoices.retrieve",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_invoice,
            output_serializer=InvoiceOutputSerializer,
        ),
    )
    out = handle_tools_call({"name": "invoices.retrieve", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    # many=False → an object, not a list.
    assert isinstance(out["structuredContent"], dict)
    assert out["structuredContent"]["number"] == "A"


def test_retrieve_kind_without_serializer_passes_instance_through() -> None:
    """No output serializer → render whatever the selector returned verbatim."""
    server = _server()

    def _selector() -> dict[str, str]:
        return {"id": "1", "label": "X"}

    server.register_selector_tool(
        name="x.retrieve",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_selector),
    )
    out = handle_tools_call({"name": "x.retrieve", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"id": "1", "label": "X"}


@pytest.mark.django_db
def test_retrieve_kind_forwards_output_serializer_context() -> None:
    """``spec.output_serializer_context`` participates in single-instance render."""
    invoice = Invoice.objects.create(number="A", amount_cents=100, sent=True)

    class _ContextProbe(drf_serializers.ModelSerializer):
        extra = drf_serializers.SerializerMethodField()

        class Meta:
            model = Invoice
            fields = ["number", "extra"]

        def get_extra(self, _: Invoice) -> str:
            return self.context["tag"]

    def _ctx_provider(view: Any, request: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"tag": "via-spec"}

    server = _server()
    server.register_selector_tool(
        name="invoices.retrieve",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda: invoice,
            output_serializer=_ContextProbe,
            output_serializer_context=_ctx_provider,
        ),
    )
    out = handle_tools_call({"name": "invoices.retrieve", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"number": "A", "extra": "via-spec"}


# ---------- Pagination type guard (kind=LIST) ----------


@pytest.mark.django_db
def test_pagination_over_list_returning_selector_paginates_in_memory() -> None:
    """A LIST selector returning a plain list paginates via len()+slice —
    not the opaque ``list.count()`` crash the old hasattr guard produced."""
    for i in range(5):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i)

    def _as_list() -> list[Invoice]:
        return list(Invoice.objects.all().order_by("amount_cents"))

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_as_list,
            output_serializer=InvoiceOutputSerializer,
        ),
        paginate=True,
    )
    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"page": 2, "limit": 2}}, _ctx(server)
    )
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert payload["page"] == 2
    assert payload["totalPages"] == 3  # ceil(5/2)
    assert payload["hasNext"] is True
    assert [item["number"] for item in payload["items"]] == ["INV-2", "INV-3"]


@pytest.mark.django_db
def test_pagination_over_non_sized_return_raises_clear_error() -> None:
    """A non-QuerySet, non-sequence return (a generator) raises a precise
    error instead of an opaque ``count()`` / slice failure."""

    def _generator() -> Any:
        yield from ()

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_generator,
            output_serializer=InvoiceOutputSerializer,
        ),
        paginate=True,
    )
    with pytest.raises(TypeError, match="must return a QuerySet or a sized"):
        handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))


# ---------- object-level permissions on the resolved row ----------
#
# A spec's ``permission_classes`` are two checks, not one. The class-level
# ``has_permission`` is wrapped into the binding and runs before dispatch; the
# object-level ``has_object_permission`` needs the row, which only dispatch
# resolves — so it runs through ``dispatch_spec``'s ``on_target_resolved``
# hook. Over HTTP a DRF view runs both. This transport must too, or a spec
# whose ownership test lives in ``has_object_permission`` is enforced
# everywhere except here.


class _OnlyMine(drf_permissions.BasePermission):
    """Anyone may call the tool; only the owner may see the row."""

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:  # noqa: ARG002
        return obj.number == "MINE"


def _invoice_by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _register_guarded_retrieve(server: MCPServer) -> None:
    server.register_selector_tool(
        name="invoices.get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice_by_pk,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[_OnlyMine],
        ),
    )


@pytest.mark.django_db
def test_object_permissions_run_against_the_resolved_row() -> None:
    """Another principal's row is refused, not rendered."""
    theirs = Invoice.objects.create(number="THEIRS", amount_cents=100)
    server = _server()
    _register_guarded_retrieve(server)

    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": theirs.pk}}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db
def test_object_permissions_still_serve_a_permitted_row() -> None:
    """The guard is a check, not a blanket denial."""
    mine = Invoice.objects.create(number="MINE", amount_cents=100)
    server = _server()
    _register_guarded_retrieve(server)

    out = handle_tools_call({"name": "invoices.get", "arguments": {"pk": mine.pk}}, _ctx(server))

    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "MINE"


@pytest.mark.django_db
def test_a_list_target_runs_only_the_class_level_check() -> None:
    """``has_object_permission`` is a per-row concept; a LIST resolves a set.

    The guard is handed the queryset, not a model, so it must not try a row
    check against it — otherwise every guarded LIST tool would deny.
    """
    Invoice.objects.create(number="THEIRS", amount_cents=100)
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[_OnlyMine],
        ),
    )

    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))

    assert isinstance(out, dict)
    assert [row["number"] for row in out["structuredContent"]] == ["THEIRS"]
