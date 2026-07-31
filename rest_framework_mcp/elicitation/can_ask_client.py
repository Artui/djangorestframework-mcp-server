from __future__ import annotations

from typing import Any


def can_ask_client(client_capabilities: dict[str, Any]) -> bool:
    """Whether *this request* may be answered with a form to fill in.

    The spec's MUST NOT: a server *"MUST NOT send an ``inputRequests`` that the
    client has not declared support for in its capabilities"*, naming
    elicitation as the example. Read per request, like every other modern-era
    capability — a legacy client declared its capabilities once, at
    ``initialize``, so this map is empty for it and the answer is ``False``
    without an era branch anywhere.

    ⚠ **``{}`` means yes.** The schema's own example of "form mode only" is a
    bare ``"elicitation": {}``, because form was the only mode before this
    revision added URL mode; a client that omits the sub-keys is declaring the
    original behaviour. A *non-empty* object is read as an enumeration, so
    ``{"url": {}}`` — url mode and nothing else — is a client this package
    cannot ask, since forms are all it builds.
    """
    elicitation: Any = client_capabilities.get("elicitation")
    if not isinstance(elicitation, dict):
        return False
    return not elicitation or "form" in elicitation


__all__ = ["can_ask_client"]
