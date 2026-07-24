"""``build_selector_tool_input_schema`` — reflected selector shape in the wire schema.

The MCP selector ``inputSchema`` now folds in drf-services'
``spec_to_json_schema`` reflection — the *same* source the Pydantic-AI
``SpecToolset`` consumes — so a selector's own parameters and an
``**extras: Unpack[TypedDict]`` are advertised over MCP without the consumer
restating them on an ``input_serializer`` or an explicit ``UrlKwarg``. The
explicit sources (``input_serializer`` fields, ``url_kwargs``) still win over a
reflected key of the same name.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_mcp import UrlKwarg
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.schema.selector_tool_schema import build_selector_tool_input_schema


def _binding(selector: Any, **kwargs: Any) -> SelectorToolBinding:
    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector)
    return SelectorToolBinding(name="t", description=None, spec=spec, **kwargs)


def test_reflects_plain_callable_params() -> None:
    # A retrieve selector's own parameters are advertised (``user`` seed skipped).
    def _get_widget(user: Any, pk: int) -> Any: ...

    schema = build_selector_tool_input_schema(_binding(_get_widget))
    assert schema == {"type": "object", "properties": {"pk": {"type": "integer"}}}


def test_skips_transport_seeds() -> None:
    def _sel(user: Any, request: Any, view: Any, pk: int) -> Any: ...

    schema = build_selector_tool_input_schema(_binding(_sel))
    assert schema["properties"] == {"pk": {"type": "integer"}}


class _NestedRouteExtras(TypedDict):
    parent_pk: int  # required route capture
    label: NotRequired[str]


def test_expands_unpack_extras_with_required() -> None:
    # The headline case: a nested-route selector reading URL kwargs from its
    # ``**extras`` now advertises them over MCP (``parent_pk`` required) instead
    # of a hidden KeyError — no explicit ``UrlKwarg`` needed for discovery.
    def _sel(user: Any, **extras: Unpack[_NestedRouteExtras]) -> Any: ...

    schema = build_selector_tool_input_schema(_binding(_sel))
    assert schema == {
        "type": "object",
        "properties": {"parent_pk": {"type": "integer"}, "label": {"type": "string"}},
        "required": ["parent_pk"],
    }


class _OverrideSerializer(serializers.Serializer):
    pk = serializers.CharField(help_text="explicit override")


def test_input_serializer_wins_over_reflected_param() -> None:
    # A curated ``input_serializer`` field overrides the reflected param of the
    # same name — the explicit declaration is authoritative.
    def _sel(user: Any, pk: int) -> Any: ...

    schema = build_selector_tool_input_schema(_binding(_sel, input_serializer=_OverrideSerializer))
    assert schema["properties"]["pk"] == {"type": "string", "description": "explicit override"}


def test_url_kwarg_wins_over_reflected_extra() -> None:
    # A key that is both a reflected extra and a registered ``UrlKwarg`` uses the
    # UrlKwarg's advertised schema (the intentional, authoritative declaration),
    # while staying in ``required`` from the TypedDict.
    def _sel(user: Any, **extras: Unpack[_NestedRouteExtras]) -> Any: ...

    schema = build_selector_tool_input_schema(
        _binding(_sel, url_kwargs=(UrlKwarg("parent_pk", type="string", description="owning"),))
    )
    assert schema["properties"]["parent_pk"] == {"type": "string", "description": "owning"}
    assert schema["required"] == ["parent_pk"]


def test_no_reflected_shape_is_bare_object() -> None:
    # A selector with no declarable inputs stays a bare object (no empty
    # ``properties`` / ``required`` noise).
    def _sel(user: Any) -> Any: ...

    assert build_selector_tool_input_schema(_binding(_sel)) == {"type": "object", "properties": {}}
