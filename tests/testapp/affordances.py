"""One declaration of ``affordances``, shared by the tests that advertise and call it.

Every condition is a callable rather than a condition on the row, so nothing here
needs a database: drf-services answers a callable once per call and adds the answer
to each mapping row a selector returns. Two conditions, so a schema that enumerates
only the one a row happened to fail is told apart from one that enumerates the
declaration.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend


class OrderSerializer(serializers.Serializer):
    number = serializers.CharField()


CANCEL_ORDER: ServiceSpec[Any, Any, Any] = ServiceSpec(
    service=lambda **_: None,
    atomic=False,
    affordances=[
        Affordance(code="already_shipped", reason="The order has shipped.", when=lambda **_: True),
        Affordance(code="books_closed", reason="The books are closed.", when=lambda **_: False),
    ],
)
"""Met on its first condition and refused on its second, so every call answers
``books_closed``."""

DECLARED_CODES: list[str] = ["already_shipped", "books_closed"]


def order_selector_spec(kind: SelectorKind, **overrides: Any) -> SelectorSpec[Any, Any]:
    """A selector spec rendering one order, declaring ``cancel`` as an affordance."""
    rows: Any = {"number": "A-1"} if kind is SelectorKind.RETRIEVE else [{"number": "A-1"}]
    fields: dict[str, Any] = {
        "kind": kind,
        "selector": lambda **_: rows,
        "output_serializer": OrderSerializer,
        "affordances": {"cancel": CANCEL_ORDER},
        **overrides,
    }
    return SelectorSpec(**fields)


def fresh_server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
