"""Ordering on a selector tool, and which channel owns it.

A ``FilterSet``'s ``OrderingFilter`` is the canonical declaration. It
subclasses ``ChoiceFilter``, so drf-services' reflection maps it to an enum
exactly like any other choice filter — which means a spec carrying one
advertises ``ordering`` in the tool's ``inputSchema`` with nothing declared at
registration.

⚠ That advertisement used to be a lie. ``ordering`` sat in
``RESERVED_POST_FETCH_KEYS`` and was stripped from the single mapping that
served as both the selector's kwarg pool *and* the FilterSet's data, so the
value never reached the filter and nothing applied it: the model asked for
newest-first, got whatever order the queryset had, and was told nothing. These
tests pin the promise and the delivery to each other.
"""

from __future__ import annotations

import json
from typing import Any

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer


class OrderedInvoiceFilterSet(django_filters.FilterSet):
    """The public vocabulary is ``amount`` / ``-amount``, not the ORM path.

    ``param_map`` is the whole point of the collision this file guards: the
    FilterSet's choices are consumer-facing names, while ``ordering_fields``
    values are raw paths handed to ``.order_by()``. Two vocabularies, one key.
    """

    sent = django_filters.BooleanFilter()
    # Named ``ordering`` because that is the convention django-filter and DRF
    # both train, and it is the name the transport used to strip. A FilterSet
    # that spells it anything else was never affected — which is exactly how
    # the defect stayed invisible to whoever last wrote a test for this.
    ordering = django_filters.OrderingFilter(
        fields=(("amount_cents", "amount"),),
    )

    class Meta:
        model = Invoice
        fields = ["sent"]


class PlainInvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()

    class Meta:
        model = Invoice
        fields = ["sent"]


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


def _list_invoices(*, user: Any) -> Any:  # noqa: ARG001
    return Invoice.objects.all()


def _rows(out: Any) -> list[dict[str, Any]]:
    """The payload rows out of a tools/call result."""
    assert isinstance(out, dict), out
    assert not out.get("isError"), out
    return json.loads(out["content"][0]["text"])


def _register_ordered(server: MCPServer, **kwargs: Any) -> Any:
    return server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=OrderedInvoiceFilterSet,
        ),
        **kwargs,
    )


# ---------- the filter's ordering is advertised, and honoured ----------


def test_the_filters_ordering_is_advertised_with_nothing_declared() -> None:
    server = _server()
    _register_ordered(server)

    out = handle_tools_list(None, _ctx(server))

    assert isinstance(out, dict)
    tool = next(t for t in out["tools"] if t["name"] == "invoices.list")
    properties = tool["inputSchema"]["properties"]
    assert "ordering" in properties, (
        "an OrderingFilter subclasses ChoiceFilter, so the reflection should "
        f"surface it as an enum; got {sorted(properties)}"
    )
    assert set(properties["ordering"]["enum"]) == {"amount", "-amount"}


@pytest.mark.django_db
def test_an_advertised_ordering_actually_orders_the_rows() -> None:
    """⭐ The test the wave exists for.

    Before the fix this passed every check except the one that matters: the
    call succeeded, the payload was well-formed, and the rows came back in
    insertion order. Assert the *order*, not the absence of an error.
    """
    Invoice.objects.create(number="mid", amount_cents=200)
    Invoice.objects.create(number="low", amount_cents=100)
    Invoice.objects.create(number="high", amount_cents=300)
    server = _server()
    _register_ordered(server)

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "amount"}},
        _ctx(server),
    )

    assert [row["number"] for row in _rows(out)] == ["low", "mid", "high"]


@pytest.mark.django_db
def test_the_descending_choice_orders_the_other_way() -> None:
    Invoice.objects.create(number="mid", amount_cents=200)
    Invoice.objects.create(number="low", amount_cents=100)
    Invoice.objects.create(number="high", amount_cents=300)
    server = _server()
    _register_ordered(server)

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "-amount"}},
        _ctx(server),
    )

    assert [row["number"] for row in _rows(out)] == ["high", "mid", "low"]


@pytest.mark.django_db
def test_ordering_composes_with_a_filter_and_with_pagination() -> None:
    """The three read-shaping channels are applied together, in that order.

    Filtering narrows, the FilterSet orders what remains, and the tool layer
    pages the result — so the second page of an ordered, filtered set is the
    rows a caller would predict.
    """
    for number, cents, sent in [
        ("a", 100, True),
        ("b", 200, True),
        ("c", 300, True),
        ("skip", 400, False),
    ]:
        Invoice.objects.create(number=number, amount_cents=cents, sent=sent)
    server = _server()
    _register_ordered(server, paginate=True)

    out = handle_tools_call(
        {
            "name": "invoices.list",
            "arguments": {"sent": True, "ordering": "-amount", "page": 2, "limit": 2},
        },
        _ctx(server),
    )

    payload = _rows(out)
    assert [row["number"] for row in payload["items"]] == ["a"]
    assert payload["totalPages"] == 2


@pytest.mark.django_db
def test_the_pagination_knobs_do_not_leak_into_the_selectors_kwargs() -> None:
    """The FilterSet gets the unstripped arguments; the callable does not.

    A selector taking ``**kwargs`` is the shape that would notice, and it is
    why the strip existed in the first place — so widening the FilterSet's view
    must not widen the callable's.
    """
    Invoice.objects.create(number="only", amount_cents=100)
    seen: dict[str, Any] = {}

    def greedy_selector(*, user: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        seen.update(kwargs)
        return Invoice.objects.all()

    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=greedy_selector,
            output_serializer=InvoiceOutputSerializer,
            filter_set=OrderedInvoiceFilterSet,
        ),
        paginate=True,
    )

    handle_tools_call(
        {
            "name": "invoices.list",
            "arguments": {"ordering": "amount", "page": 1, "limit": 5},
        },
        _ctx(server),
    )

    assert "page" not in seen
    assert "limit" not in seen
    assert "ordering" not in seen


# ---------- the retired second vocabulary ----------


def test_declaring_both_channels_is_refused_at_registration() -> None:
    """Two vocabularies under one key — the failure has to be at configuration
    time, because at request time one of them simply wins and says nothing."""
    server = _server()

    with pytest.raises(ImproperlyConfigured, match="ordering is declared twice"):
        _register_ordered(server, ordering_fields=["amount_cents"])


def test_ordering_fields_alone_still_works_and_warns() -> None:
    """A spec with no ordering on its filter has no other route yet, so the
    legacy channel keeps working — loudly."""
    server = _server()

    with pytest.warns(DeprecationWarning, match="ordering_fields is deprecated"):
        server.register_selector_tool(
            name="invoices.list",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_invoices,
                output_serializer=InvoiceOutputSerializer,
                filter_set=PlainInvoiceFilterSet,
            ),
            ordering_fields=["amount_cents"],
        )

    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    tool = next(t for t in out["tools"] if t["name"] == "invoices.list")
    assert set(tool["inputSchema"]["properties"]["ordering"]["enum"]) == {
        "amount_cents",
        "-amount_cents",
    }


@pytest.mark.django_db
def test_the_legacy_channel_still_orders() -> None:
    Invoice.objects.create(number="mid", amount_cents=200)
    Invoice.objects.create(number="low", amount_cents=100)
    server = _server()
    with pytest.warns(DeprecationWarning):
        server.register_selector_tool(
            name="invoices.list",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_invoices,
                output_serializer=InvoiceOutputSerializer,
            ),
            ordering_fields=["amount_cents"],
        )

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "-amount_cents"}},
        _ctx(server),
    )

    assert [row["number"] for row in _rows(out)] == ["mid", "low"]
