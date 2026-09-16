from __future__ import annotations

from typing import Any

from rest_framework_services import serializer_to_json_schema


def build_input_schema(input_serializer: type | None, *, partial: bool = False) -> dict[str, Any]:
    """Build a JSON Schema for a tool's input.

    MCP-named wrapper over drf-services' ``serializer_to_json_schema``, which
    takes a DRF ``Serializer`` subclass, a bare ``@dataclass`` type, or ``None``
    (the tool takes no input), and drops ``required`` when ``partial``. The
    conversion is shared with every other transport rather than reproduced here.

    Anything else raises ``TypeError`` from drf-services 0.50 — the error its
    dispatch path already raised for such a tool at call time. Earlier releases
    answered ``{"type": "object"}`` instead, so the tool advertised no arguments
    and no call to it could succeed. Nothing here catches the error, and nothing
    needs to: registration refuses those shapes first
    (``adapters.utils.validate_serializer_shapes``). Left to this function, one
    such tool would fail the whole ``tools/list`` rather than drop out of it,
    because every schema is rebuilt on each request.
    """
    return serializer_to_json_schema(input_serializer, partial=partial)


__all__ = ["build_input_schema"]
