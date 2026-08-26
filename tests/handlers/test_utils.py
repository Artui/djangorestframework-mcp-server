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

import pytest
from rest_framework import serializers as drf_serializers
from rest_framework_services import UNSET
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp import QueryParam, UrlKwarg
from rest_framework_mcp.handlers.utils import (
    split_query_params,
    split_url_kwargs,
    validate_input_against_serializer,
)


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


# ---------- "no default declared" is spelled two ways across sister releases ----------


@pytest.mark.parametrize("no_default", [None, UNSET])
def test_a_url_kwarg_declaring_no_default_seeds_nothing(no_default: Any) -> None:
    """Both spellings of "no default" must leave the kwarg absent.

    ``UrlKwarg.default`` / ``QueryParam.default`` are declared in the sister
    package, where the sentinel for "no default" is version-dependent: it was
    ``None``, and becomes the package's ``UNSET`` so that a deliberate
    ``default=None`` can be expressed. Both are pinned here because the failure
    mode of testing only for ``None`` is silent: ``UNSET is not None``, so the
    sentinel object itself would be seeded into ``view.kwargs`` as if it were a
    real value, and the first thing to notice would be a query filtering on it.
    """
    params, values = split_url_kwargs({}, (UrlKwarg("project_pk", default=no_default),))
    assert params == {}
    assert values == {}


@pytest.mark.parametrize("no_default", [None, UNSET])
def test_a_query_param_declaring_no_default_seeds_nothing(no_default: Any) -> None:
    """The query-param splitter carries the same tolerance, for the same reason."""
    params, values = split_query_params({}, (QueryParam("fields", default=no_default),))
    assert params == {}
    assert values == {}


def test_a_declared_default_is_still_seeded() -> None:
    _, url_values = split_url_kwargs({}, (UrlKwarg("project_pk", default="P1"),))
    _, qp_values = split_query_params({}, (QueryParam("fields", default="id"),))
    assert url_values == {"project_pk": "P1"}
    assert qp_values == {"fields": "id"}


@pytest.mark.parametrize("no_default", [None, UNSET])
def test_no_default_still_reaches_the_required_check(no_default: Any) -> None:
    """Neither spelling may be mistaken for a value that satisfies ``required``."""
    with pytest.raises(ServiceValidationError):
        split_url_kwargs({}, (UrlKwarg("project_pk", required=True, default=no_default),))
