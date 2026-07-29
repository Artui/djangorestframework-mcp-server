"""Unit coverage for ``handlers.utils`` helpers.

Output-serializer context used to be resolved here; it now goes through
drf-services' ``render_spec_output``, which owns the layering (DRF baseline +
the spec's provider, bound through the keyword pool) for every transport. The
end-to-end guards for that live in ``test_spec_shaping_and_context.py``, which
exercises it through real tool dispatch rather than against a local copy.

What remains here is the read-path input validator, whose ``context`` this
transport still supplies itself.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers

from rest_framework_mcp.handlers.utils import validate_input_against_serializer


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
