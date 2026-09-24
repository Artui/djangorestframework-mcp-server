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

import json
from typing import Any

from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_mcp import QueryParam, UrlKwarg
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.agent_conventions import PAGED_QUERY_PARAM_SCOPE
from rest_framework_mcp.schema.selector_tool_schema import build_selector_tool_input_schema
from rest_framework_mcp.schema.service_tool_schema import build_service_tool_input_schema


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


# ---------- what a read-shaping param applies to on a page ----------

_QUERY = QueryParam("query", description="django-restql fieldset, e.g. {id, name}")


def _list_binding(**kwargs: Any) -> SelectorToolBinding:
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda: [])
    return SelectorToolBinding(name="t", description=None, spec=spec, **kwargs)


def test_a_paged_tool_says_its_query_param_applies_to_each_item() -> None:
    """The ``outputSchema`` shows the envelope, so the param has to say it is not it."""
    schema = build_selector_tool_input_schema(_list_binding(paginate=True, query_params=(_QUERY,)))

    assert schema["properties"]["query"] == {
        "type": "string",
        "description": f"django-restql fieldset, e.g. {{id, name}}. {PAGED_QUERY_PARAM_SCOPE}",
    }


def test_the_sentence_stands_alone_when_nothing_was_declared() -> None:
    bare = QueryParam("fields")

    schema = build_selector_tool_input_schema(_list_binding(paginate=True, query_params=(bare,)))

    assert schema["properties"]["fields"]["description"] == PAGED_QUERY_PARAM_SCOPE


def test_an_unpaginated_list_advertises_the_param_as_declared() -> None:
    schema = build_selector_tool_input_schema(_list_binding(query_params=(_QUERY,)))

    assert schema["properties"]["query"] == _QUERY.json_schema()


def test_a_retrieve_advertises_the_param_as_declared() -> None:
    def _get(pk: int) -> Any: ...

    schema = build_selector_tool_input_schema(_binding(_get, query_params=(_QUERY,)))

    assert schema["properties"]["query"] == _QUERY.json_schema()


def test_a_service_tool_advertises_the_param_as_declared() -> None:
    def _touch() -> None: ...

    binding = ToolBinding(
        name="s",
        description=None,
        spec=ServiceSpec(service=_touch, atomic=False),
        query_params=(_QUERY,),
    )

    schema = build_service_tool_input_schema(binding)

    assert schema["properties"]["query"] == _QUERY.json_schema()
    assert PAGED_QUERY_PARAM_SCOPE not in json.dumps(schema)
