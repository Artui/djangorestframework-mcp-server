from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.conf import get_setting


def resolve_structured_output(
    *,
    include_output_schema_override: bool | None,
    include_structured_content_override: bool | None,
    binding_name: str,
) -> tuple[bool, bool]:
    """Collapse the structured-output tri-state overrides against globals.

    Returns ``(output_schema, structured_content)`` — the effective
    booleans for whether the binding should advertise ``outputSchema`` in
    ``tools/list`` and emit ``structuredContent`` in ``tools/call``.

    Each override is tri-state: ``None`` defers to the corresponding
    ``REST_FRAMEWORK_MCP`` setting (``INCLUDE_OUTPUT_SCHEMA`` /
    ``INCLUDE_STRUCTURED_CONTENT``), ``True`` / ``False`` force the
    behaviour regardless of the global.

    The MCP spec (2025-06-18) requires that any tool which declares an
    ``outputSchema`` always returns conforming ``structuredContent``. The
    reverse — emitting ``structuredContent`` without an ``outputSchema``
    — is allowed. This function enforces the asymmetric rule: if the
    effective combination would advertise the schema while suppressing
    the content, ``ImproperlyConfigured`` is raised so the misconfig
    surfaces immediately rather than producing a non-compliant response.
    """
    output_schema: bool = (
        include_output_schema_override
        if include_output_schema_override is not None
        else bool(get_setting("INCLUDE_OUTPUT_SCHEMA"))
    )
    structured_content: bool = (
        include_structured_content_override
        if include_structured_content_override is not None
        else bool(get_setting("INCLUDE_STRUCTURED_CONTENT"))
    )
    if output_schema and not structured_content:
        raise ImproperlyConfigured(
            f"Tool {binding_name!r}: outputSchema would be advertised but "
            "structuredContent is disabled. The MCP spec requires conforming "
            "structuredContent whenever outputSchema is declared. Either set "
            "INCLUDE_OUTPUT_SCHEMA=False (or include_output_schema=False on "
            "the binding) to drop the schema, or re-enable "
            "INCLUDE_STRUCTURED_CONTENT / include_structured_content=True."
        )
    return output_schema, structured_content


__all__ = ["resolve_structured_output"]
