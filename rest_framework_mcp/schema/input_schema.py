from __future__ import annotations

from typing import Any

from rest_framework_services import serializer_to_json_schema


def build_input_schema(input_serializer: type | None, *, partial: bool = False) -> dict[str, Any]:
    """Build a JSON Schema for a tool's input.

    MCP-named wrapper over drf-services' ``serializer_to_json_schema``, which
    takes a DRF ``Serializer`` subclass, a bare ``@dataclass`` type, or ``None``
    (the tool takes no input), and drops ``required`` when ``partial``. The
    conversion is shared with every other transport rather than reproduced here.
    """
    return serializer_to_json_schema(input_serializer, partial=partial)


__all__ = ["build_input_schema"]
