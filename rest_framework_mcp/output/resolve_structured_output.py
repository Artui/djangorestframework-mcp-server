from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured


def resolve_structured_output(
    *,
    include_output_schema_override: bool | None,
    include_structured_content_override: bool | None,
    binding_name: str,
    default_output_schema: bool,
    default_structured_content: bool,
) -> tuple[bool, bool]:
    """Collapse the structured-output tri-state overrides against globals.

    Each override is tri-state: ``None`` defers to the passed-in server-level
    default, ``True`` / ``False`` force the behaviour regardless.

    The MCP spec requires a tool declaring an ``outputSchema`` to always return
    conforming ``structuredContent``; the reverse is allowed. This enforces that
    asymmetry rather than emitting a non-compliant response.

    Returns:
        ``(output_schema, structured_content)`` — whether the binding advertises
        ``outputSchema`` in ``tools/list`` and emits ``structuredContent`` in
        ``tools/call``.

    Raises:
        django.core.exceptions.ImproperlyConfigured: If the effective
            combination would advertise the schema while suppressing the
            content.
    """
    output_schema: bool = (
        include_output_schema_override
        if include_output_schema_override is not None
        else default_output_schema
    )
    structured_content: bool = (
        include_structured_content_override
        if include_structured_content_override is not None
        else default_structured_content
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
