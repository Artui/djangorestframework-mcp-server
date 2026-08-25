"""Agent-facing wording for a tool's handles — the description and the line.

Both are prompts, so both live in the transport that knows a model is reading.
drf-services supplies the markings and no wording at all: what a reader should
*do* with an identifier depends on the reader.
"""

from __future__ import annotations

from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.field_audience import FieldAudience

HANDLE_DESCRIPTION = (
    "Opaque identifier. Pass it to other tools that ask for one; do not read it out."
)
"""Fallback ``outputSchema`` wording for a handle that declares none of its own.

Per field, beside the field it describes, which is where a model reads it. The
sentence below is the one that has nowhere else to go and rides the tool
description instead."""

_HANDLE_LINE = (
    "Fields described as opaque identifiers are for other tool calls, not for the "
    "reader: pass them on where a tool asks for one, and never read them out."
)


def append_agent_conventions(description: str | None, projection: AgentProjection) -> str | None:
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
