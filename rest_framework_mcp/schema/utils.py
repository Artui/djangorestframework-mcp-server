from __future__ import annotations

import dataclasses
from typing import Any, get_args, get_origin, get_type_hints

from rest_framework import serializers

# Map DRF field types to JSON Schema fragments. Order matters: more specific
# subclasses (BooleanField, IntegerField) must come before broader ones
# (CharField) because we walk this list and use the first ``isinstance`` hit.
_DRF_FIELD_TO_SCHEMA: list[tuple[type[serializers.Field], dict[str, Any]]] = [
    (serializers.BooleanField, {"type": "boolean"}),
    (serializers.IntegerField, {"type": "integer"}),
    (serializers.FloatField, {"type": "number"}),
    (serializers.DecimalField, {"type": "string", "format": "decimal"}),
    (serializers.DateTimeField, {"type": "string", "format": "date-time"}),
    (serializers.DateField, {"type": "string", "format": "date"}),
    (serializers.TimeField, {"type": "string", "format": "time"}),
    (serializers.UUIDField, {"type": "string", "format": "uuid"}),
    (serializers.EmailField, {"type": "string", "format": "email"}),
    (serializers.URLField, {"type": "string", "format": "uri"}),
    (serializers.IPAddressField, {"type": "string"}),
    (serializers.JSONField, {}),
    (serializers.CharField, {"type": "string"}),
]


def field_to_schema(field: serializers.Field) -> dict[str, Any]:
    """Convert a single DRF field into a JSON Schema fragment.

    Recurses into nested serializers and list fields. Unknown field types
    fall back to ``{}`` (any), so an exotic field type never breaks discovery.
    """
    if isinstance(field, serializers.ListField):
        child: serializers.Field | None = field.child
        item_schema: dict[str, Any] = field_to_schema(child) if child is not None else {}
        return {"type": "array", "items": item_schema}
    if isinstance(field, serializers.ListSerializer):
        # The DRF stub types ``ListSerializer.child`` as ``Field``, but in
        # practice it's always a ``Serializer`` (otherwise ``ListField`` would
        # be the right choice). The runtime fallback handles the ``None`` case.
        list_child: serializers.Serializer | None = field.child  # ty: ignore[invalid-assignment]
        if list_child is None:
            return {"type": "array", "items": {}}
        return {"type": "array", "items": serializer_to_schema(list_child)}
    if isinstance(field, serializers.Serializer):
        return serializer_to_schema(field)
    if isinstance(field, serializers.ChoiceField):
        return {"enum": list(field.choices.keys())}
    for cls, schema in _DRF_FIELD_TO_SCHEMA:
        if isinstance(field, cls):
            return dict(schema)
    return {}


def serializer_to_schema(serializer: serializers.Serializer) -> dict[str, Any]:
    """Convert a DRF serializer instance into a JSON Schema object.

    Required fields (DRF ``required=True``) populate ``required``; everything
    else is optional. Field-level help text becomes ``description``.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in serializer.fields.items():
        if field.read_only:
            continue
        properties[name] = field_to_schema(field)
        if field.help_text:
            properties[name]["description"] = str(field.help_text)
        if field.required:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Best-effort mapping from Python type annotations to JSON Schema.

    Used when introspecting bare dataclasses (e.g. when no ``input_serializer``
    is configured but the service callable's signature includes a dataclass
    parameter). Falls back to ``{}`` for unknown types.
    """
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    origin: Any = get_origin(annotation)
    if origin is list:
        (item_type,) = get_args(annotation) or (Any,)
        return {"type": "array", "items": _python_type_to_schema(item_type)}
    return {}


def dataclass_to_schema(cls: type) -> dict[str, Any]:
    """Convert a plain ``@dataclass`` type into a JSON Schema object.

    Public surface kept small: this is only used by ``input_schema`` /
    ``output_schema`` when no serializer is provided. Field annotations are
    resolved via :func:`typing.get_type_hints` so dataclasses declared under
    ``from __future__ import annotations`` (string annotations) work.
    """
    hints: dict[str, Any] = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        annotation: Any = hints.get(f.name, f.type)
        properties[f.name] = _python_type_to_schema(annotation)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:  # type: ignore[misc]
            required.append(f.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


__all__ = ["dataclass_to_schema", "field_to_schema", "serializer_to_schema"]
