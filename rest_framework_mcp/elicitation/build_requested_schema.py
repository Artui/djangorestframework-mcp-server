from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.constants import ELICITATION_SCALAR_TYPES


def build_requested_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Turn ``AdditionalInputRequired.schema`` into a spec-legal ``requestedSchema``.

    The service declares only the properties — ``{"confirmed": {"type":
    "boolean"}}`` — because that is all a transport-neutral exception can
    usefully say. The object wrapper and the ``required`` list are MCP's, so
    they are added here.

    **Required is inferred from ``default``.** A property that declares one has
    said what to do without an answer; one that has not is something the service
    is waiting for. That is the only signal available and it matches how the
    same dict would read as a serializer field.

    ⚠ **Raises rather than shipping a schema the client must refuse.** The
    subset MCP allows here is narrow — *"only top-level properties, without
    nesting"*, primitives and enums — and a client that receives anything else
    is entitled to reject the whole result. Sending it anyway would surface as
    an unexplained client-side failure a long way from the service that caused
    it, so an unusable schema fails loudly, at the one moment its author can
    still be told which property is wrong. It is a programming error in the
    service, which is what ``ImproperlyConfigured`` is for.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, definition in schema.items():
        _reject_unusable(name, definition)
        properties[name] = definition
        if "default" not in definition:
            required.append(name)

    built: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        # Omitted rather than sent empty when every property carries a default:
        # ``required: []`` and no ``required`` mean the same thing, and the
        # shorter one is what the spec's own examples show.
        built["required"] = required
    return built


def _reject_unusable(name: str, definition: Any) -> None:
    """Fail on anything outside ``PrimitiveSchemaDefinition``."""
    if not isinstance(definition, dict):
        raise ImproperlyConfigured(
            f"AdditionalInputRequired asked for {name!r} with {type(definition).__name__} "
            "instead of a schema object. Each value must be a JSON Schema fragment, "
            'e.g. {"type": "boolean"}.'
        )
    # ⚠ ``str`` first. JSON Schema lets ``type`` be a *list* of types, which is
    # both something an author might reasonably write and something that raises
    # ``TypeError: unhashable`` on the membership test below — a crash where a
    # refusal was wanted.
    declared: Any = definition.get("type")
    if not isinstance(declared, str):
        declared = None
    if declared in ELICITATION_SCALAR_TYPES:
        return
    if declared == "array" and _is_multi_select(definition.get("items")):
        return
    raise ImproperlyConfigured(
        f"AdditionalInputRequired asked for {name!r} with type {declared!r}, which a client "
        "cannot render. An elicitation form takes top-level fields only: "
        f"{', '.join(sorted(ELICITATION_SCALAR_TYPES))}, or an array of enum values. "
        "Nested objects have to be flattened into separate fields."
    )


def _is_multi_select(items: Any) -> bool:
    """Whether ``items`` is the one array shape the spec allows — a list of enum
    values, titled (``anyOf``) or not (``enum``)."""
    if not isinstance(items, dict):
        return False
    return isinstance(items.get("enum"), list) or isinstance(items.get("anyOf"), list)


__all__ = ["build_requested_schema"]
