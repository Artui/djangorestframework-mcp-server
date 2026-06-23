from __future__ import annotations

from typing import Any

from rest_framework_services import serializer_to_json_schema


def build_input_schema(input_serializer: type | None, *, partial: bool = False) -> dict[str, Any]:
    """Build a JSON Schema for a tool's input.

    Thin MCP-named wrapper over the sister repo's
    :func:`~rest_framework_services.serializer_to_json_schema` (drf-services
    0.19), which handles a DRF ``Serializer`` subclass, a bare ``@dataclass``
    type, or ``None`` (the tool takes no input), and drops the ``required`` list
    when ``partial`` (the ``spec.partial`` contract). The serializer →
    JSON-Schema conversion — DRF field mappings, nested serializers, choice
    enums, drf-spectacular ``@extend_schema_*`` overrides — is shared with every
    other transport rather than reproduced here.
    """
    return serializer_to_json_schema(input_serializer, partial=partial)


__all__ = ["build_input_schema"]
