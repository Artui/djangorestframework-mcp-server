from __future__ import annotations

from typing import Any

from rest_framework_services import output_to_json_schema
from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.selector_kind import SelectorKind


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
    """
    return output_to_json_schema(
        output_serializer, kind=kind, paginate=paginate, projection=projection
    )


__all__ = ["build_output_schema"]
