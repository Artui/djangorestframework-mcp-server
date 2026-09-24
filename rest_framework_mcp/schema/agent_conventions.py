"""Agent-facing wording for a tool's handles — the description and the line.

Both are prompts, so both live in the transport that knows a model is reading.
drf-services supplies the markings and no wording at all: what a reader should
*do* with an identifier depends on the reader.
"""

from __future__ import annotations

from rest_framework_services.types.audience_projection import AudienceProjection
from rest_framework_services.types.field_audience import FieldAudience

HANDLE_DESCRIPTION = (
    "Opaque identifier. Pass it to other tools that ask for one; do not read it out."
)
"""Fallback ``outputSchema`` wording for a handle that declares none of its own.

Per field, beside the field it describes, which is where a model reads it. The
sentence below is the one that has nowhere else to go and rides the tool
description instead."""

PAGED_QUERY_PARAM_SCOPE = (
    "On a paged result it applies to each item in `items`, never to the page "
    "envelope (`items`, `page`, `totalPages`, `hasNext`)."
)
"""What a read-shaping ``QueryParam`` applies to on a tool that returns a page.

The ``outputSchema`` of a paged tool describes the envelope, because that is what
the result is, and a field-selection param names fields. So the one shape a model
has been shown is exactly the wrong one to select against: ``{items{id, name}}``
reads naturally off the schema, and a serializer rendering one row at a time has
no ``items`` field. This sentence says which of the two shapes the param
addresses, beside the param (``selector_tool_schema``) and again in the
``isError`` text when a selection is refused while rendering
(``handlers.utils.read_shaping_error_result``), which is where a model that got
it wrong reads next.

Only a paged tool gets it. A service tool's result and an unpaginated list have
no envelope, so on those the sentence would describe a shape the model never
sees. Written here rather than supplied by drf-services for the reason the module
docstring gives: it is a prompt, and the wording belongs to the transport that
knows a model is reading."""

_HANDLE_LINE = (
    "Fields described as opaque identifiers are for other tool calls, not for the "
    "reader: pass them on where a tool asks for one, and never read them out."
)


def append_agent_conventions(description: str | None, projection: AudienceProjection) -> str | None:
    """Add the handle convention to a tool's description, when it has handles.

    Conditional on something being able to act on it. A tool whose output
    carries no handle gains nothing from being told how to treat one, and a
    description is read on every listing — advice a model cannot use is not free,
    it is budget spent teaching it about a field it will never see.

    The per-field wording lives in ``outputSchema``, where a model reads it
    beside the field it describes; this is the one sentence that has nowhere
    else to go.
    """
    handles = [
        name
        for name, marking in projection.fields.items()
        if marking.audience is FieldAudience.HANDLE
    ]
    if not handles:
        return description
    line = _HANDLE_LINE
    if projection.label:
        line = f"Identify records by `{projection.label}`. {line}"
    return f"{description}\n\n{line}" if description else line


__all__ = ["append_agent_conventions"]
