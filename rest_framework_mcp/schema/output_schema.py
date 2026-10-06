from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services import output_to_json_schema
from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.schema.agent_conventions import HANDLE_DESCRIPTION


def build_output_schema(
    output_serializer: type | None,
    *,
    kind: SelectorKind | None = None,
    paginate: bool = False,
    projection: AudienceProjection | None = None,
    affordances: Mapping[str, ServiceSpec[Any, Any, Any]] | None = None,
    may_be_empty: bool = False,
    handle_description: str | None = HANDLE_DESCRIPTION,
) -> dict[str, Any] | None:
    """Build a JSON Schema for a tool's output, or ``None`` if not declared.

    MCP-named wrapper over drf-services' ``output_to_json_schema``. ``None``
    when there is no ``output_serializer``; otherwise the shape matches what the
    dispatch pipeline returns:

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema.
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
    - ``kind=LIST, paginate=True`` — ``{items, page, totalPages, hasNext}``.

    A read-only ``BaseSerializer`` subclass has no fields to describe and also
    answers ``None``. Anything that is neither a ``BaseSerializer`` subclass nor a
    dataclass type raises ``TypeError``, as it would fail to render; registration
    refuses those shapes first (``adapters.utils.validate_serializer_shapes``), so
    one never reaches the per-request ``tools/list`` build.

    ``projection`` applies the output serializer's agent markings, so the
    advertised schema describes what a caller actually receives rather than what
    the serializer renders in full. It is generated from the same declaration the
    dispatch path projects the payload through, which is what stops a tool
    advertising a field its results no longer carry.

    ``affordances`` is the ``affordances`` mapping on the selector spec the
    payload renders through, and declares the ``affordances`` object drf-services
    adds to every rendered item, with each name's refusal ``code`` enumerated. It
    is the other key the schema would otherwise miss: no serializer declares it,
    so the serializer-derived item says nothing about it, and a schema that does
    not forbid extra keys lets every result conform anyway. Pass the same mapping
    the render path reads, which each binding exposes as ``rendered_affordances``,
    so the schema and the payload come from one declaration.

    The wording for an unlabelled handle is supplied here rather than upstream:
    it is a sentence written for a model, and drf-services does not know that a
    model is what is reading. ``handle_description`` is that wording, the
    server's ``AgentConventions.handle_field_description``; ``None`` leaves such
    a handle undescribed, and a handle declaring its own wording keeps it either
    way.

    ``may_be_empty`` says a successful call can present nothing, which is served
    as ``structuredContent: {}`` (each binding answers it as
    ``can_present_nothing``). MCP needs the schema's root to stay an object
    and the served ``{}`` to conform, so a ``null`` cannot be added the way
    drf-services' ``allow_none=`` adds one, and this never passes that keyword.
    Instead the root keeps its ``type`` and ``properties``, and its
    ``required`` list moves into
    ``"anyOf": [{"required": [...]}, {"maxProperties": 0}]``. A full row
    satisfies the first branch, ``{}`` the second, and a non-empty row missing
    a required field neither. An item schema with nothing required already
    admits ``{}`` and is returned as derived. So is a ``LIST`` schema, whatever
    ``may_be_empty`` says: a list result is a list, empty at worst, and the
    paginated envelope's four keys stay required.

    The guard is one ``or``-chain, so branch coverage cannot see a deleted
    condition. Each one is held by a test that fails without it:
    ``test_a_retrieve_that_cannot_present_nothing_keeps_its_schema_strict``
    (``not may_be_empty``),
    ``test_may_be_empty_returns_a_list_schema_as_derived`` (``kind is LIST``,
    its ``envelope`` case),
    ``test_may_be_empty_with_no_output_serializer_is_still_no_schema``
    (``schema is None``) and
    ``test_may_be_empty_leaves_a_schema_with_nothing_required_as_derived``
    (``not schema.get("required")``).
    """
    schema: dict[str, Any] | None = output_to_json_schema(
        output_serializer,
        kind=kind,
        paginate=paginate,
        projection=projection,
        handle_description=handle_description,
        affordances=affordances,
    )
    if (
        not may_be_empty
        or kind is SelectorKind.LIST
        or schema is None
        or not schema.get("required")
    ):
        return schema
    admitting: dict[str, Any] = {k: v for k, v in schema.items() if k != "required"}
    admitting["anyOf"] = [{"required": schema["required"]}, {"maxProperties": 0}]
    return admitting


__all__ = ["build_output_schema"]
