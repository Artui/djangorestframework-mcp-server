from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import ELICITATION_CREATE_METHOD


@dataclass(frozen=True)
class ElicitRequest:
    """One question to put to the user, in the spec's ``elicitation/create`` shape.

    Form mode only. The spec's other mode, ``url``, hands the user a link and
    expects them to complete something out of band; it exists for the sensitive
    values a form is explicitly *not* for (*"elicit non-sensitive information
    from the user via a form"*), and nothing on this side knows how to mint such
    a URL. A service that needs one has a redirect to build, not a schema to
    declare.

    ``mode`` is emitted explicitly even though ``"form"`` is its default. A
    client that supports both modes branches on it, and the one byte saved by
    relying on the default is not worth a client having to know that rule.
    """

    message: str
    """What to show the user. The service's own message, verbatim — it is the
    half of ``AdditionalInputRequired`` that survives even when a transport
    cannot render a form."""

    requested_schema: dict[str, Any]
    """The restricted JSON Schema describing the fields to collect. Built by
    :func:`~rest_framework_mcp.elicitation.build_requested_schema.build_requested_schema`,
    which is also what rejects a shape the client would have to refuse."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": ELICITATION_CREATE_METHOD,
            "params": {
                "mode": "form",
                "message": self.message,
                "requestedSchema": self.requested_schema,
            },
        }


__all__ = ["ElicitRequest"]
