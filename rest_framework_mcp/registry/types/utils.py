"""Shared validation and derivation for the three tool bindings.

In a sibling ``utils.py`` rather than its own leaf module because it is
internal infrastructure for ``registry.types``, not part of the exported
type surface.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

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


def rendered_kind(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> SelectorKind:
    """Whether ``spec``'s rendered result is one object or a list of them.

    The cardinality drf-services' ``dispatch_spec`` gives the result, which is
    what the payload is rendered ``many=`` by:

    - A ``SelectorSpec`` answers its own ``kind``.
    - A ``ServiceSpec`` answers ``LIST`` only when its ``output_selector_spec``
      is a ``LIST`` *and has a selector*: the re-fetch is what produces the set,
      and with no selector the service's own return value renders as one object
      whatever the nested ``kind`` says. Anything else is a single object.

    One answer read by both halves of a tool -- each binding's ``rendered_kind``,
    which picks the advertised ``outputSchema`` shape, and the chain renderer,
    which picks ``many`` -- because they were once answered separately. Only a
    selector tool's schema was kind-aware, so every other ``LIST`` output
    advertised one object while serving an array, and a chain rendered a
    service step's ``LIST`` re-fetch as a single object and failed.
    """
    if isinstance(spec, SelectorSpec):
        return spec.kind
    nested = spec.output_selector_spec
    if nested is not None and nested.selector is not None and nested.kind is SelectorKind.LIST:
        return SelectorKind.LIST
    return SelectorKind.RETRIEVE


__all__ = ["rendered_kind", "validate_content_kind"]
