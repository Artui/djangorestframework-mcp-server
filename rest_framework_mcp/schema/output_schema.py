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
    model is what is reading.
    """
    return output_to_json_schema(
        output_serializer,
        kind=kind,
        paginate=paginate,
        projection=projection,
        handle_description=HANDLE_DESCRIPTION,
        affordances=affordances,
    )


__all__ = ["build_output_schema"]
