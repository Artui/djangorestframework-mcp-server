"""``append_agent_conventions`` — teach the model what a tool's handles are."""

from __future__ import annotations

from rest_framework_services.types.agent_projection import AgentProjection
from rest_framework_services.types.field_audience import FieldAudience

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
