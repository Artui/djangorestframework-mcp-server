"""``selector_to_resource`` — what a resource registration does and does not carry.

The adapter insists on a full ``SelectorSpec`` because the spec is the unit of
registration everywhere in this package, but ``resources/read`` dispatches the
bare selector callable: it never builds the queryset, never resolves an output
serializer context, never runs a precondition. Taking a spec that declares those
and dropping them silently is the failure this module pins — a
``preconditions`` gate that holds on every other transport and simply does not
run here looks exactly like success.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.adapters.selector_to_resource import selector_to_resource


def _rows(user: Any = None) -> list[dict[str, Any]]:
    return [{"id": 1}]


def _require_subscription(**kwargs: Any) -> None:
    raise AssertionError("precondition must never be reachable through a resource read")


def _with_tenant(**kwargs: Any) -> dict[str, Any]:
    return {"tenant": "t1"}


class _Out(serializers.Serializer):
    id = serializers.IntegerField()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allow_none", True),
        ("annotations", {"n": 1}),
        ("extend_queryset", lambda qs, view, request: qs),
        ("filter_set", object),
        ("metadata", {"k": "v"}),
        ("output_serializer_context", _with_tenant),
        ("prefetch_related", ["tags"]),
        ("preconditions", [_require_subscription]),
        ("progress_reporter", lambda **kw: None),
        ("select_related", ["owner"]),
    ],
)
def test_a_spec_field_the_read_path_cannot_apply_is_refused_at_registration(
    field: str, value: Any
) -> None:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_rows, **{field: value})
    with pytest.raises(ValueError, match=field):
        selector_to_resource(name="r", uri_template="r://x", selector=spec)


def test_the_refusal_names_every_dropped_field_at_once() -> None:
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        select_related=["owner"],
        preconditions=[_require_subscription],
    )
    with pytest.raises(ValueError) as excinfo:
        selector_to_resource(name="r", uri_template="r://x", selector=spec)
    message = str(excinfo.value)
    assert "'preconditions'" in message
    assert "'select_related'" in message
    # And says what to do instead, since the spec itself is not the problem.
    assert "selector tool" in message


def test_the_carried_fields_still_register() -> None:
    """The five the read path honours are unaffected."""
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        output_serializer=_Out,
        kwargs=lambda view: {},
    )
    binding = selector_to_resource(name="r", uri_template="r://x", selector=spec)
    assert binding.selector is _rows
    assert binding.kind is SelectorKind.LIST
    assert binding.output_serializer is _Out
    assert binding.kwargs_provider is spec.kwargs
