"""Ordering on a selector tool, and which channel owns it.

Two channels survive the removal of the ``ordering_fields`` knob, and neither
is a registration kwarg: a ``FilterSet``'s ``OrderingFilter``, and a sort
parameter the selector callable declares for itself. The first is preferred —
it validates against published choices before anything reaches the ORM — and
the second is what a project with no ``django-filter`` dependency uses. Both
are exercised below.

An ``OrderingFilter`` subclasses ``ChoiceFilter``, so drf-services' reflection
maps it exactly like any other choice filter — which means a spec carrying one
advertises ``ordering`` in the tool's ``inputSchema`` with nothing declared at
registration.

Since drf-services 0.47.0 that mapping is a labelled ``oneOf`` rather than a
bare ``enum``: the choice labels travel with their constants, so a model reading
the schema is told that ``-amount`` means "Amount (descending)" instead of
having to infer it from a leading minus sign.

That advertisement used to be a lie. ``ordering`` sat in
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
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
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

    The mapping is the point: a consumer-facing name is what the model sees and
    sends, and the ORM path behind it stays an implementation detail the tool
    never publishes.
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
        f"surface it as a choice schema; got {sorted(properties)}"
    )
    # The labels are asserted, not just the constants: they are what makes the
    # descending direction readable, and dropping them would leave the tool
    # advertising two opaque strings with a sign between them.
    assert properties["ordering"]["oneOf"] == [
        {"const": "amount", "title": "Amount"},
        {"const": "-amount", "title": "Amount (descending)"},
    ]


@pytest.mark.django_db
def test_an_advertised_ordering_actually_orders_the_rows() -> None:
    """The test the wave exists for.

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
    # ``ordering`` is *not* withheld any more. It belongs to whatever declared
    # it -- here the ``FilterSet`` -- and reaches the callable exactly as every
    # other filter argument already did. Withholding it by name was what made a
    # selector unable to own a parameter of that name.
    assert seen["ordering"] == "amount"


# ---------- an unrecognised value ----------


@pytest.mark.django_db
def test_an_unrecognised_ordering_value_is_rejected_by_the_filter() -> None:
    """A value outside the advertised choices is refused, not guessed at.

    ``ordering`` is validated like every other filter field now, so a mistyped
    value raises DRF's ``ValidationError`` out of queryset shaping — which the
    ViewSet turns into DRF's own 400 rather than a JSON-RPC ``-32602``. That is
    the shape *every* invalid filter value on a selector tool already had; the
    retired knob was the one channel that quietly dropped a bad value and
    answered with rows in an order nobody asked for.

    Pinned rather than asserted-as-desirable: routing it through the
    ``-32602`` envelope the service-tool path uses would be an improvement, and
    a deliberate one, so it should have to edit this test.
    """
    Invoice.objects.create(number="mid", amount_cents=200)
    Invoice.objects.create(number="low", amount_cents=100)
    server = _server()
    _register_ordered(server)

    with pytest.raises(drf_serializers.ValidationError, match="not one of the available choices"):
        handle_tools_call(
            {"name": "invoices.list", "arguments": {"ordering": "--amount"}},
            _ctx(server),
        )


# ---------- the removed second vocabulary ----------


def test_ordering_fields_is_no_longer_a_registration_kwarg() -> None:
    """The knob is gone from the signature, not merely ignored.

    A silently-accepted ``ordering_fields=`` would be worse than the removal:
    the caller would keep declaring an ordering that nothing applies, and the
    tool would advertise none. ``TypeError`` at registration is the intended
    breaking change — deprecated in 0.30.0, removed here.
    """
    server = _server()

    with pytest.raises(TypeError, match="ordering_fields"):
        _register_ordered(server, ordering_fields=["amount_cents"])


# ---------- the selector's own sort parameter ----------
#
# The other route to an orderable tool, for a project with no django-filter
# dependency: the selector declares a sort parameter and drf-services reflects
# it into the schema like any other. It works — with one name it cannot use.


def _list_invoices_sorted(*, user: Any, sort: str = "-amount_cents") -> Any:  # noqa: ARG001
    return Invoice.objects.all().order_by(sort)


@pytest.mark.django_db
def test_a_selector_declared_sort_parameter_is_advertised_and_applied() -> None:
    """No ``filter_set`` anywhere: the callable's own parameter is the channel.

    Reflected into the ``inputSchema`` as a plain string and spread into the
    selector's kwargs at dispatch, so a project without the ``[filter]`` extra
    still ships an orderable tool. The FilterSet route is the better one where
    there is a FilterSet — this value reaches ``.order_by()`` with only whatever
    checking the selector itself does — but it is not the only one.
    """
    Invoice.objects.create(number="mid", amount_cents=200)
    Invoice.objects.create(number="low", amount_cents=100)
    Invoice.objects.create(number="high", amount_cents=300)
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices_sorted,
            output_serializer=InvoiceOutputSerializer,
        ),
    )

    listed = handle_tools_list(None, _ctx(server))
    assert isinstance(listed, dict)
    assert listed["tools"][0]["inputSchema"]["properties"]["sort"] == {"type": "string"}

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"sort": "amount_cents"}},
        _ctx(server),
    )

    assert [row["number"] for row in _rows(out)] == ["low", "mid", "high"]


def _list_invoices_ordering_param(*, user: Any, ordering: str = "-amount_cents") -> Any:  # noqa: ARG001
    return Invoice.objects.all().order_by(ordering)


@pytest.mark.django_db
def test_a_selector_may_own_a_parameter_named_ordering() -> None:
    """No name is reserved for sorting any more, including this one.

    While ``ordering_fields`` existed, the pipeline sorted the queryset itself
    and held ``ordering`` in ``RESERVED_POST_FETCH_KEYS`` so a ``**kwargs``
    selector would not receive an argument it never declared. A parameter
    *named* ``ordering`` was caught by that strip by name: reflection advertised
    it and dispatch dropped it, so the selector ran on its default -- the same
    promise-without-delivery shape 0.30.0 fixed for the FilterSet channel.

    Removing the knob removed the consumer, so the reservation went with it.
    Sorting now belongs to whatever declares it, under whatever name it likes:
    a ``FilterSet``'s ``OrderingFilter``, or a parameter on the callable.
    """
    Invoice.objects.create(number="mid", amount_cents=200)
    Invoice.objects.create(number="low", amount_cents=100)
    Invoice.objects.create(number="high", amount_cents=300)
    server = _server()
    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_list_invoices_ordering_param,
            output_serializer=InvoiceOutputSerializer,
        ),
    )

    listed = handle_tools_list(None, _ctx(server))
    assert isinstance(listed, dict)
    assert "ordering" in listed["tools"][0]["inputSchema"]["properties"]

    out = handle_tools_call(
        {"name": "invoices.list", "arguments": {"ordering": "amount_cents"}},
        _ctx(server),
    )

    # Ascending was asked for, and ascending is what ran -- the argument now
    # reaches the callable that declared it.
    assert [row["number"] for row in _rows(out)] == ["low", "mid", "high"]
