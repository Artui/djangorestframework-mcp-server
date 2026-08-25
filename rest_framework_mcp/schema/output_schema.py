from __future__ import annotations

from typing import Any

from rest_framework_services import output_to_json_schema
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.schema.agent_conventions import HANDLE_DESCRIPTION


def build_output_schema(
    output_serializer: type | None,
    *,
    kind: SelectorKind | None = None,
    paginate: bool = False,
    projection: AgentProjection | None = None,
) -> dict[str, Any] | None:
    """Build a JSON Schema for a tool's output, or ``None`` if not declared.

    MCP-named wrapper over drf-services' ``output_to_json_schema``. ``None``
    when there is no ``output_serializer``; otherwise the shape matches what the
    dispatch pipeline returns:

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema.
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
    - ``kind=LIST, paginate=True`` — ``{items, page, totalPages, hasNext}``.

    ``projection`` applies the output serializer's agent markings, so the
    advertised schema describes what a caller actually receives rather than what
    the serializer renders in full. It is generated from the same declaration the
    dispatch path projects the payload through, which is what stops a tool
    advertising a field its results no longer carry.

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
    )


__all__ = ["build_output_schema"]
