"""Phase 10-pre coverage: sister-repo 0.12+ ``SelectorSpec`` / ``ServiceSpec`` fields.

The shaping fields (``select_related``, ``prefetch_related``,
``annotations``, ``extend_queryset``) are exercised against a real
SQLite model. ``connection.queries`` is the cheap-but-honest probe for
``select_related`` (a single JOIN, no follow-up query). ``annotations``
appears as a synthesised attribute on each row.

``input_serializer_context`` / ``output_serializer_context`` are
exercised through a service tool — the spec callable's return is what
shows up in the serializer's ``self.context``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework.request import Request
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer


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


def _list_all_invoices() -> Any:
    return Invoice.objects.all()


# ---------- Selector-tool shaping ----------


@pytest.mark.django_db
def test_selector_spec_empty_shaping_is_noop() -> None:
    """Falsy/empty shaping fields don't try to apply (no FK, no annotate, no extend)."""
    Invoice.objects.create(number="A", amount_cents=100, sent=False)
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=InvoiceOutputSerializer,
            select_related=(),
            prefetch_related=(),
            annotations={},
            extend_queryset=None,
        ),
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 1


@pytest.mark.django_db
def test_selector_spec_annotations_attach_to_each_row() -> None:
    Invoice.objects.create(number="A", amount_cents=100, sent=False)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)

    class _Out(drf_serializers.ModelSerializer):
        annotated_double = drf_serializers.IntegerField()

        class Meta:
            model = Invoice
            fields = ["id", "number", "annotated_double"]

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=_Out,
            annotations={"annotated_double": models.F("amount_cents") * 2},
        ),
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    rows = out["structuredContent"]
    assert len(rows) == 2
    assert sorted(r["annotated_double"] for r in rows) == [200, 400]


@pytest.mark.django_db
def test_selector_spec_extend_queryset_runs_last() -> None:
    """``extend_queryset`` sees the already-shaped queryset and can narrow it."""
    Invoice.objects.create(number="A", amount_cents=100, sent=False)
    Invoice.objects.create(number="B", amount_cents=200, sent=False)
    Invoice.objects.create(number="C", amount_cents=300, sent=False)

    received_args: dict[str, Any] = {}

    def _narrow(qs: Any, view: Any, request: Any) -> Any:
        received_args["view"] = view
        received_args["request"] = request
        return qs.filter(amount_cents__gte=200)

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=InvoiceOutputSerializer,
            extend_queryset=_narrow,
        ),
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    rows = out["structuredContent"]
    assert sorted(r["number"] for r in rows) == ["B", "C"]
    assert received_args["view"].action == "invoices.list"
    assert isinstance(received_args["request"], Request)


@pytest.mark.django_db
def test_selector_spec_prefetch_related_applies() -> None:
    """``prefetch_related`` lookups land on the queryset before ``extend_queryset``."""
    Invoice.objects.create(number="A", amount_cents=100, sent=False)
    captured: dict[str, Any] = {}

    def _capture_and_strip(qs: Any, view: Any, request: Any) -> Any:  # noqa: ARG001
        captured["lookups"] = list(qs._prefetch_related_lookups)
        # Return a fresh queryset so the bogus lookup is never evaluated
        # against a real relation (the testapp model has no FKs).
        return Invoice.objects.all()

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=InvoiceOutputSerializer,
            prefetch_related=("bogus_relation_not_evaluated",),
            extend_queryset=_capture_and_strip,
        ),
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert captured["lookups"] == ["bogus_relation_not_evaluated"]


@pytest.mark.django_db
def test_selector_spec_select_related_lookups_recorded() -> None:
    Invoice.objects.create(number="A", amount_cents=100, sent=False)
    captured: dict[str, Any] = {}

    def _capture_and_strip(qs: Any, view: Any, request: Any) -> Any:  # noqa: ARG001
        captured["select_related"] = qs.query.select_related
        return Invoice.objects.all()

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=InvoiceOutputSerializer,
            select_related=("invalid_fk",),
            extend_queryset=_capture_and_strip,
        ),
    )
    handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    # ``QuerySet.query.select_related`` is a dict keyed by lookup when set.
    assert "invalid_fk" in captured["select_related"]


@pytest.mark.django_db
def test_selector_spec_shaping_requires_a_queryset() -> None:
    """Shaping fields on a selector that returns a non-queryset fail loudly.

    Routing through ``dispatch_spec`` adopts drf-services' strict contract:
    ``select_related`` … ``extend_queryset`` / ``filter_set`` require a
    ``QuerySet``. A list-returning selector with shaping configured is a
    misconfiguration and raises ``ImproperlyConfigured`` (a developer error,
    surfaced loudly) rather than silently skipping the shaping.
    """

    def _return_list() -> list[dict[str, str]]:
        return [{"id": 1, "number": "X"}]

    server = _server()
    server.register_selector_tool(
        name="static.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_return_list,
            select_related=("ignored",),
            annotations={"ignored": models.F("amount_cents")},
            extend_queryset=lambda qs, view, req: qs,
        ),
    )
    with pytest.raises(ImproperlyConfigured):
        handle_tools_call({"name": "static.list", "arguments": {}}, _ctx(server))


# ---------- Selector-tool output_serializer_context ----------


@pytest.mark.django_db
def test_selector_spec_output_serializer_context_flows_into_render() -> None:
    Invoice.objects.create(number="A", amount_cents=100, sent=False)

    class _ContextProbeSerializer(drf_serializers.ModelSerializer):
        extra = drf_serializers.SerializerMethodField()

        class Meta:
            model = Invoice
            fields = ["id", "number", "extra"]

        def get_extra(self, _: Invoice) -> str:
            return self.context["tag"]

    def _ctx_provider(view: Any, request: Any) -> Mapping[str, Any]:  # noqa: ARG001
        return {"tag": "via-spec"}

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=_ContextProbeSerializer,
            output_serializer_context=_ctx_provider,
        ),
    )
    out = handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"][0]["extra"] == "via-spec"


# ---------- ServiceSpec input + output context ----------


@pytest.mark.django_db
def test_service_spec_input_serializer_context_flows_into_validation() -> None:
    received: dict[str, Any] = {}

    class _CtxAwareInput(drf_serializers.Serializer):
        number = drf_serializers.CharField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            received.update(self.context)
            return attrs

    def _create(*, data: dict[str, Any]) -> dict[str, Any]:
        return {"number": data["number"]}

    def _ctx_provider(view: Any, request: Any) -> Mapping[str, Any]:  # noqa: ARG001
        return {"tenant": "acme"}

    server = _server()
    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=_create,
            input_serializer=_CtxAwareInput,
            input_serializer_context=_ctx_provider,
            atomic=False,
        ),
    )
    handle_tools_call({"name": "invoices.create", "arguments": {"number": "X"}}, _ctx(server))
    assert received["tenant"] == "acme"


@pytest.mark.django_db
def test_service_spec_output_serializer_context_flows_into_render() -> None:
    class _Out(drf_serializers.Serializer):
        tag = drf_serializers.SerializerMethodField()

        def get_tag(self, _: Any) -> str:
            return self.context["who"]

    def _svc() -> dict[str, str]:
        return {}

    def _ctx_provider(view: Any, request: Any) -> Mapping[str, Any]:  # noqa: ARG001
        return {"who": "rendered"}

    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(
            service=_svc,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=_Out,
                output_serializer_context=_ctx_provider,
            ),
            atomic=False,
        ),
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tag": "rendered"}


# ---------- Resolved-data extras (sister-repo 0.15+) ----------
#
# A provider may declare ``result`` (service tool), ``instance`` (selector
# RETRIEVE), or ``page`` (selector LIST) and receive the exact value being
# serialised — passed only when declared. ``invoice_pk`` below reads off
# the resolved data to prove it is the real object, not a placeholder.


@pytest.mark.django_db
def test_service_tool_provider_receives_result_extra() -> None:
    seen: dict[str, Any] = {}

    class _Out(drf_serializers.ModelSerializer):
        echoed = drf_serializers.SerializerMethodField()

        class Meta:
            model = Invoice
            fields = ["id", "number", "echoed"]

        def get_echoed(self, _: Invoice) -> int:
            return self.context["seen_pk"]

    def _create() -> Invoice:
        return Invoice.objects.create(number="NEW", amount_cents=1)

    def _ctx_provider(view: Any, request: Any, *, result: Any) -> Mapping[str, Any]:  # noqa: ARG001
        seen["result"] = result
        return {"seen_pk": result.pk}

    server = _server()
    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=_create,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=_Out,
                output_serializer_context=_ctx_provider,
            ),
            atomic=False,
        ),
    )
    out = handle_tools_call({"name": "invoices.create", "arguments": {}}, _ctx(server))
    assert isinstance(out, dict)
    assert isinstance(seen["result"], Invoice)
    assert out["structuredContent"]["echoed"] == seen["result"].pk


@pytest.mark.django_db
def test_service_tool_result_is_post_output_selector_instance() -> None:
    """With an output selector, the provider sees the re-fetched row."""
    raw = Invoice.objects.create(number="RAW", amount_cents=1)
    seen: dict[str, Any] = {}

    def _svc() -> Invoice:
        return raw

    def _refetch(*, result: Invoice) -> Any:
        # The MCP output-selector contract returns the final value directly
        # (no queryset .first() materialisation), so re-fetch the instance.
        return Invoice.objects.get(pk=result.pk)

    def _ctx_provider(view: Any, request: Any, *, result: Any) -> Mapping[str, Any]:  # noqa: ARG001
        seen["is_raw"] = result is raw
        seen["pk"] = result.pk
        return {}

    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(
            service=_svc,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_refetch,
                output_serializer=InvoiceOutputSerializer,
                output_serializer_context=_ctx_provider,
            ),
            atomic=False,
        ),
    )
    handle_tools_call({"name": "t", "arguments": {}}, _ctx(server))
    assert seen["is_raw"] is False  # re-fetched → distinct instance
    assert seen["pk"] == raw.pk


@pytest.mark.django_db
def test_selector_retrieve_tool_provider_receives_instance_extra() -> None:
    invoice = Invoice.objects.create(number="ONE", amount_cents=1)
    seen: dict[str, Any] = {}

    def _get_one() -> Invoice:
        return Invoice.objects.get(number="ONE")

    def _ctx_provider(view: Any, request: Any, *, instance: Any) -> Mapping[str, Any]:  # noqa: ARG001
        seen["instance"] = instance
        return {}

    server = _server()
    server.register_selector_tool(
        name="invoices.get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_one,
            output_serializer=InvoiceOutputSerializer,
            output_serializer_context=_ctx_provider,
        ),
    )
    handle_tools_call({"name": "invoices.get", "arguments": {}}, _ctx(server))
    assert isinstance(seen["instance"], Invoice)
    assert seen["instance"].pk == invoice.pk


@pytest.mark.django_db
def test_selector_list_tool_unpaginated_provider_receives_full_result() -> None:
    for i in range(3):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i)
    seen: dict[str, Any] = {}

    def _ctx_provider(view: Any, request: Any, *, page: Any) -> Mapping[str, Any]:  # noqa: ARG001
        seen["count"] = len(list(page))
        return {}

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=InvoiceOutputSerializer,
            output_serializer_context=_ctx_provider,
        ),
    )
    handle_tools_call({"name": "invoices.list", "arguments": {}}, _ctx(server))
    assert seen["count"] == 3


@pytest.mark.django_db
def test_selector_list_tool_paginated_provider_receives_page_slice() -> None:
    for i in range(5):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i)
    seen: dict[str, Any] = {}

    def _ctx_provider(view: Any, request: Any, *, page: Any) -> Mapping[str, Any]:  # noqa: ARG001
        seen["page_numbers"] = [inv.number for inv in page]
        return {}

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=InvoiceOutputSerializer,
            output_serializer_context=_ctx_provider,
        ),
        ordering_fields=["amount_cents"],
        paginate=True,
    )
    handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "amount_cents", "page": 1, "limit": 2}},
        _ctx(server),
    )
    # Provider saw only the 2-row page, not all 5 rows.
    assert seen["page_numbers"] == ["INV-0", "INV-1"]


@pytest.mark.django_db
def test_paginated_page_read_is_a_single_query(django_assert_num_queries: Any) -> None:
    """Reading ids off ``page`` reuses the queryset's result cache — the
    renderer iterates the same object, so no second query is issued."""
    for i in range(5):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i)

    def _ctx_provider(view: Any, request: Any, *, page: Any) -> Mapping[str, Any]:  # noqa: ARG001
        # One batched query keyed on the page's ids.
        ids = [inv.pk for inv in page]
        return {"ids": list(Invoice.objects.filter(pk__in=ids).values_list("pk", flat=True))}

    class _Out(drf_serializers.ModelSerializer):
        class Meta:
            model = Invoice
            fields = ["id", "number"]

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_all_invoices,
            output_serializer=_Out,
            output_serializer_context=_ctx_provider,
        ),
        paginate=True,
    )
    # count(1) + page fetch(1) + provider's batched query(1) = 3.
    with django_assert_num_queries(3):
        handle_tools_call(
            {"name": "invoices.list", "arguments": {"page": 1, "limit": 2}}, _ctx(server)
        )
