from __future__ import annotations

from typing import Any

from rest_framework_services import output_to_json_schema
from rest_framework_services.types.selector_kind import SelectorKind


def build_output_schema(
    output_serializer: type | None,
    *,
    kind: SelectorKind | None = None,
    paginate: bool = False,
) -> dict[str, Any] | None:
    """Build a JSON Schema for a tool's output, or ``None`` if not declared.

    Thin MCP-named wrapper over the sister repo's
    :func:`~rest_framework_services.output_to_json_schema` (drf-services 0.19),
    which makes ``outputSchema`` optional (``None`` when no
    ``output_serializer``) and matches what the dispatch pipeline returns:

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema (single-instance).
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
    - ``kind=LIST, paginate=True`` — the pagination envelope
      ``{items, page, totalPages, hasNext}``.

    The serializer → JSON-Schema conversion is shared with every other
    transport rather than reproduced here.
    """
    return output_to_json_schema(output_serializer, kind=kind, paginate=paginate)


__all__ = ["build_output_schema"]
