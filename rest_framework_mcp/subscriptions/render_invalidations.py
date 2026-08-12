from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TEMPLATE_VAR = re.compile(r"\{([^}]+)\}")


def render_invalidations(
    templates: tuple[str, ...],
    *,
    payload: Any,
    arguments: Mapping[str, Any],
) -> tuple[str, ...]:
    """Turn a binding's ``invalidates=`` templates into the URIs that changed.

    Templates use the same ``{var}`` syntax as ``uri_template`` on a resource,
    so a consumer writes ``invoices://{pk}`` in both places and the two cannot
    drift into different dialects.

    **Rendered against the result merged with the call's arguments, result
    winning.** The result is authoritative after a write — a service that
    reassigns an invoice's number leaves the argument stale — while the
    arguments are the only source when the result does not carry the key, as a
    delete returning nothing still has ``{pk}`` in its input.

    **A template that cannot render is dropped, never raised.** The write has
    committed by the time this runs, so failing the call over a formatting
    mistake would report a failure for work that succeeded; a missed
    notification is recovered by the client's next read. Dropping is silent, so
    a template naming a key nothing produces is caught by the
    registration-time check, not here.

    Duplicates collapse while order is kept, so declaring both
    ``invoices://{pk}`` and ``invoices://`` publishes two topics and declaring
    the same template twice publishes one.
    """
    values: dict[str, Any] = {**dict(arguments), **_result_keys(payload)}
    rendered: list[str] = []
    for template in templates:
        uri: str | None = _render(template, values)
        if uri is not None:
            rendered.append(uri)
    return tuple(dict.fromkeys(rendered))


def _render(template: str, values: Mapping[str, Any]) -> str | None:
    out: list[str] = []
    last: int = 0
    for match in _TEMPLATE_VAR.finditer(template):
        name: str = match.group(1)
        if name not in values:
            return None
        value: Any = values[name]
        if value is None:
            # A present-but-null key is as unusable as a missing one: rendering
            # it as "None" would publish a topic nobody could subscribe to.
            return None
        out.append(template[last : match.start()])
        out.append(str(value))
        last = match.end()
    out.append(template[last:])
    return "".join(out)


def _result_keys(payload: Any) -> dict[str, Any]:
    """The rendered tool result's fields, if it has any addressable ones.

    A template refers to the payload the service produced, so it is read from
    the tool result's ``structuredContent`` rather than the MCP envelope
    wrapped round it — ``invalidates=`` is written against what the author's
    service returns.

    A list payload contributes nothing: ``{pk}`` over many rows has no single
    answer, and guessing one would publish a URI for an arbitrary member.
    """
    if not isinstance(payload, dict):
        return {}
    structured: Any = payload.get("structuredContent")
    return dict(structured) if isinstance(structured, dict) else {}


__all__ = ["render_invalidations"]
