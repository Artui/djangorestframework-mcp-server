"""Unit coverage for ``handlers.utils`` helpers.

Focused on ``resolve_output_context`` — the output serializer's context over
MCP: DRF's baseline (``request`` / ``format`` / ``view``) plus the spec's
``output_serializer_context`` provider merged over it, with every provider
invoked through the keyword pool exactly as the sister repo invokes it on the
HTTP path.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers

from rest_framework_mcp.handlers.utils import (
    resolve_output_context,
    validate_input_against_serializer,
)

_BASELINE = {"request": "REQ", "format": None, "view": "VIEW"}


def test_baseline_context_without_a_provider() -> None:
    # A spec with no provider still renders with the context HTTP would supply.
    assert resolve_output_context(None, "VIEW", "REQ", extras={"page": "P"}) == _BASELINE


def test_legacy_two_arg_provider_still_binds_view_and_request() -> None:
    seen: dict[str, Any] = {}

    def provider(view: Any, request: Any) -> dict[str, Any]:
        seen["args"] = (view, request)
        return {}

    resolve_output_context(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
    # Undeclared extras must not leak in.
    assert seen["args"] == ("VIEW", "REQ")


def test_provider_declaring_only_request_is_not_handed_the_view() -> None:
    """The reported crash: bound by name, not positionally.

    ``def ctx(request, **extras)`` works over HTTP and through drf-pai; before
    0.18 this path called ``provider(view, request, **declared)`` and it raised
    ``TypeError: takes 1 positional argument but 2 were given``.
    """
    seen: dict[str, Any] = {}

    def provider(request: Any, **extras: Any) -> dict[str, Any]:
        seen["request"] = request
        seen["extras"] = extras
        return {}

    resolve_output_context(provider, "VIEW", "REQ", extras={"instance": 7})
    assert seen["request"] == "REQ"
    # ``**kwargs`` opens the whole pool, view included.
    assert seen["extras"] == {"view": "VIEW", "instance": 7}


def test_declared_extra_is_passed_by_keyword() -> None:
    seen: dict[str, Any] = {}

    def provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:
        seen["result"] = result
        return {}

    resolve_output_context(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
    assert seen["result"] == "R"


def test_var_keyword_provider_receives_the_whole_pool() -> None:
    seen: dict[str, Any] = {}

    def provider(**pool: Any) -> dict[str, Any]:
        seen.update(pool)
        return {}

    resolve_output_context(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
    assert seen == {"view": "VIEW", "request": "REQ", "result": "R", "page": "P"}


def test_provider_keys_merge_over_the_baseline() -> None:
    def provider(*, instance: Any) -> dict[str, Any]:
        return {"id": instance, "request": "PROVIDER-WINS"}

    out = resolve_output_context(provider, "VIEW", "REQ", extras={"instance": 7})
    assert out == {"id": 7, "request": "PROVIDER-WINS", "format": None, "view": "VIEW"}


def test_zero_arg_provider_is_accepted() -> None:
    out = resolve_output_context(lambda: {"k": "v"}, "VIEW", "REQ", extras={})
    assert out == {**_BASELINE, "k": "v"}


def test_validator_context_is_optional() -> None:
    """``context`` omitted → a context-free serializer, as before 0.18.

    Both dispatch paths pass the baseline; the parameter stays optional for
    callers that have no request to build one from.
    """
    seen: dict[str, Any] = {}

    class _Input(drf_serializers.Serializer):
        x = drf_serializers.IntegerField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            seen["context"] = dict(self.context)
            return attrs

    assert validate_input_against_serializer({"x": 1}, _Input) == {"x": 1}
    assert seen["context"] == {}


def test_validator_context_reaches_the_serializer() -> None:
    seen: dict[str, Any] = {}

    class _Input(drf_serializers.Serializer):
        x = drf_serializers.IntegerField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            seen["context"] = dict(self.context)
            return attrs

    validate_input_against_serializer({"x": 1}, _Input, context={"request": "REQ"})
    assert seen["context"] == {"request": "REQ"}
