from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import ELICITATION_CREATE_METHOD


@dataclass(frozen=True)
class ElicitRequest:
    """One question to put to the user, in the spec's ``elicitation/create`` shape.

    Form mode only. The spec's other mode, ``url``, hands the user a link to
    complete out of band; it exists for the sensitive values a form is
    explicitly *not* for (*"elicit non-sensitive information from the user via a
    form"*), and nothing on this side knows how to mint such a URL.

    ``mode`` is emitted explicitly even though ``"form"`` is its default, so a
    client supporting both modes can branch on it without knowing that rule.
    """

    message: str
    """What to show the user: the service's own message, verbatim."""

    requested_schema: dict[str, Any]
    """The restricted JSON Schema describing the fields to collect. Built by
    ``build_requested_schema``,
    which also rejects a shape the client would have to refuse."""

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
