"""Shared validation for the three tool bindings.

Lives in a sibling ``utils.py`` rather than its own leaf module because it
is internal infrastructure for :mod:`registry.types`, not part of the
package's exported type surface.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

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

    Shared by the three tool bindings, which each validate their own fields in
    ``__post_init__`` but agree on this rule. Two things are checked, and both
    are contradictions rather than judgement calls:

    - **Media needs a mime type.** ``image`` and ``audio`` blocks carry
      ``mimeType`` as a required field: without it the client holds a base64
      string and no way to know what it decodes to.
    - **Media has no JSON projection.** ``structuredContent`` and
      ``outputSchema`` describe a JSON payload; a tool returning a PNG has
      none. Both are suppressed for media kinds, so *asking* for either is a
      declaration that cannot be honoured — better to say so at startup than
      to quietly ignore it on every call.
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


__all__ = ["validate_content_kind"]
