from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.constants import ElicitAction


@dataclass(frozen=True)
class ElicitAnswer:
    """What came back from the user, in the spec's ``ElicitResult`` shape."""

    action: ElicitAction

    content: dict[str, Any] = field(default_factory=dict)
    """The submitted fields.

    Present only for an accepted **form** — the spec omits it for a decline, a
    cancel, and for URL mode. Defaulting to empty rather than ``None`` means the
    merge downstream needs no branch: an accept with nothing in it merges
    nothing and the service asks again, which is the right outcome for a form
    the user submitted blank."""


__all__ = ["ElicitAnswer"]
