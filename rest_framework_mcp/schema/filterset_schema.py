from __future__ import annotations

import importlib
from typing import Any


def _load_django_filters() -> Any:
    """Import the optional ``django-filter`` extra; ``None`` when absent.

    Resolved through ``importlib`` so the type checker doesn't narrow the
    binding to the imported module — keeps the optional-dep contract
    consistent with how we handle ``redis``, ``opentelemetry``,
    ``python-toon``, and ``oauth2_provider``.
    """
    try:
        return importlib.import_module("django_filters")
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


_django_filters: Any = _load_django_filters()


def filterset_to_schema_properties(filter_set_class: Any) -> dict[str, dict[str, Any]]:
    """Map a django-filter ``FilterSet`` class to JSON Schema properties.

    Walks ``FilterSet.declared_filters`` plus any auto-generated filters
    (from a ``Meta`` declaration) and returns a property dict shaped like
    the ``"properties"`` key of a JSON Schema object — ready to merge into
    a tool's ``inputSchema``.

    Filter properties are always optional from MCP's perspective: they
    narrow the queryset but are not required to call the tool. The merger
    in :mod:`rest_framework_mcp.schema.input_schema` does not add filter
    names to the ``required`` array.

    Common filter classes get accurate JSON Schema mappings; exotic
    classes fall back to ``{}`` (the JSON Schema "any value" shape) so a
    custom filter never breaks tool discovery. Tests document which
    classes are precisely mapped.

    Raises ``ImportError`` when ``django-filter`` isn't installed — this
    only fires when a binding is actually constructed with
    ``filter_set=...``, so projects that don't use the integration are
    unaffected.
    """
    if _django_filters is None:  # pragma: no cover - exercised by no-extras smoke job
        raise ImportError(
            "filter_set= requires the `django-filter` package. "
            'Install with `pip install "djangorestframework-mcp-server[filter]"`.'
        )
    # ``base_filters`` is populated by the FilterSet metaclass at class
    # creation time and combines declared + Meta-generated filters. Read
    # it directly so we don't have to instantiate the FilterSet (which
    # requires a queryset/model when ``Meta`` is set).
    filters: dict[str, Any] = dict(getattr(filter_set_class, "base_filters", {}))

    properties: dict[str, dict[str, Any]] = {}
    for name, filter_obj in filters.items():
        properties[name] = _filter_to_schema(filter_obj)
    return properties


def _filter_to_schema(filter_obj: Any) -> dict[str, Any]:
    """Map a single django-filter filter instance to a JSON Schema fragment.

    Order matters: subclass checks must come before their base classes.
    Falls back to ``{}`` for unknown filter types so an exotic filter
    silently degrades to "any value" rather than breaking tool discovery.
    """
    if _django_filters is None:  # pragma: no cover
        return {}
    f = _django_filters

    # Range filters → object with min/max.
    if isinstance(filter_obj, getattr(f, "BaseRangeFilter", ())):
        return {
            "type": "object",
            "properties": {"min": _scalar_for(filter_obj), "max": _scalar_for(filter_obj)},
        }
    # In/CSV-list filters → array of the base scalar.
    if isinstance(filter_obj, getattr(f, "BaseInFilter", ())):
        return {"type": "array", "items": _scalar_for(filter_obj)}
    # MultipleChoice → array of enum.
    if isinstance(filter_obj, getattr(f, "MultipleChoiceFilter", ())):
        return {"type": "array", "items": _choice_schema(filter_obj)}
    # ``ModelChoiceFilter`` is a subclass of ``ChoiceFilter`` (and so is
    # ``ModelMultipleChoiceFilter`` of ``MultipleChoiceFilter``) — handle
    # the FK-shaped variants explicitly *before* the generic choice check.
    # The PK type isn't known without a database round-trip, so we surface
    # ``string`` (JSON Schema's most permissive scalar) and let the
    # FilterSet do the real coercion at dispatch.
    if isinstance(filter_obj, getattr(f, "ModelChoiceFilter", ())):
        return {"type": "string"}
    # Single-choice → enum.
    if isinstance(filter_obj, getattr(f, "ChoiceFilter", ())):
        return _choice_schema(filter_obj)
    return _scalar_for(filter_obj)


def _scalar_for(filter_obj: Any) -> dict[str, Any]:
    """Return the scalar JSON Schema for a filter's underlying type.

    Used both directly (for plain filters) and as the ``items`` shape for
    array-style filters (``BaseInFilter``).
    """
    if _django_filters is None:  # pragma: no cover
        return {}
    f = _django_filters

    # Order: subclasses before bases.
    if isinstance(filter_obj, getattr(f, "BooleanFilter", ())):
        return {"type": "boolean"}
    if isinstance(filter_obj, getattr(f, "UUIDFilter", ())):
        return {"type": "string", "format": "uuid"}
    if isinstance(filter_obj, getattr(f, "DateTimeFilter", ())):
        return {"type": "string", "format": "date-time"}
    if isinstance(filter_obj, getattr(f, "DateFilter", ())):
        return {"type": "string", "format": "date"}
    if isinstance(filter_obj, getattr(f, "TimeFilter", ())):
        return {"type": "string", "format": "time"}
    if isinstance(filter_obj, getattr(f, "NumberFilter", ())):
        return {"type": "number"}
    # CharFilter and anything we don't recognise.
    if isinstance(filter_obj, getattr(f, "CharFilter", ())):
        return {"type": "string"}
    return {}


def _choice_schema(filter_obj: Any) -> dict[str, Any]:
    """Build an ``enum`` schema from a ``ChoiceFilter``-derived filter.

    Falls back to ``{"type": "string"}`` when ``extra["choices"]`` isn't
    present (some custom subclasses defer choice resolution).
    """
    extra: dict[str, Any] = getattr(filter_obj, "extra", {}) or {}
    choices: Any = extra.get("choices")
    if not choices:
        return {"type": "string"}
    # Choices come as ``[(value, label), ...]`` — keep the values.
    values: list[Any] = []
    for choice in choices:
        if isinstance(choice, tuple | list) and len(choice) >= 1:
            values.append(choice[0])
        else:
            values.append(choice)
    return {"enum": values}


__all__ = ["filterset_to_schema_properties"]
