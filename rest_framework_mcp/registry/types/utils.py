"""Shared validation and derivation for the three tool bindings.

In a sibling ``utils.py`` rather than its own leaf module because it is
internal infrastructure for ``registry.types``, not part of the exported
type surface.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import AgentField, AgentProjection, build_agent_projection
from rest_framework_services.types.field_audience import FieldAudience

from rest_framework_mcp.constants import ToolContentKind

_MEDIA_KINDS: frozenset[ToolContentKind] = frozenset({ToolContentKind.IMAGE, ToolContentKind.AUDIO})


def validate_content_kind(
    *,
    name: str,
    content_kind: ToolContentKind,
    content_mime_type: str | None,
    include_structured_content: bool | None,
    include_output_schema: bool | None,
) -> None:
    """Refuse a content-kind declaration that cannot produce a valid result.

    Called from each binding's ``__post_init__``. Both checks are
    contradictions rather than judgement calls:

    - **Media needs a mime type.** ``mimeType`` is required on an ``image`` /
      ``audio`` block; without it the client holds a base64 string and no way
      to know what it decodes to.
    - **Media has no JSON projection.** ``structuredContent`` and
      ``outputSchema`` describe a JSON payload, and a tool returning a PNG has
      none. Both are suppressed for media kinds, so asking for either is a
      declaration that cannot be honoured.
    """
    if content_kind not in _MEDIA_KINDS:
        return
    if not content_mime_type:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} requires "
            "content_mime_type — the MCP spec makes mimeType mandatory on an "
            'image/audio block. Pass e.g. content_mime_type="image/png".'
        )
    if include_structured_content is True or include_output_schema is True:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} cannot be combined "
            "with include_structured_content=True or include_output_schema=True. "
            "Both describe a JSON result shape, and this tool returns binary "
            "media instead — there is nothing for them to describe."
        )


def resolve_agent_projection(
    output_serializer: type | None,
    overrides: Mapping[str, AgentField] | None,
    *,
    name: str,
) -> AgentProjection:
    """The binding's agent markings: the serializer's, plus any per-tool override.

    The serializer is authoritative — it is the one declaration three consumers
    read. ``overrides`` exists for the case one tool needs what a sibling hides:
    a lookup tool that must return the identifier its neighbour drops.

    Raises:
        django.core.exceptions.ImproperlyConfigured: If the overrides leave two
            fields claiming ``LABEL``. The serializer alone is checked upstream;
            an override can only introduce the clash here.
    """
    projection = build_agent_projection(output_serializer)
    if not overrides:
        return projection
    fields: dict[str, AgentField] = {**projection.fields, **overrides}
    labels = [n for n, marking in fields.items() if marking.audience is FieldAudience.LABEL]
    if len(labels) > 1:
        raise ImproperlyConfigured(
            f"Tool {name!r}: field_audiences leaves {labels!r} all marked as the "
            "label. A record has one name -- override the others to something else."
        )
    return AgentProjection(
        fields=fields,
        label=labels[0] if labels else None,
        choice_labels=projection.choice_labels,
        nested=projection.nested,
    )


__all__ = ["resolve_agent_projection", "validate_content_kind"]
