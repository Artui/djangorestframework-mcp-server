from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.constants import ElicitAction


@dataclass(frozen=True)
class ElicitAnswer:
    """What came back from the user, in the spec's ``ElicitResult`` shape."""

    action: ElicitAction

    content: dict[str, Any] = field(default_factory=dict)
    """The submitted fields, present only for an accepted **form** — the spec
    omits it for a decline, a cancel, and for URL mode. Empty rather than
    ``None`` so the merge downstream needs no branch: a blank submission merges
    nothing and the service asks again."""


__all__ = ["ElicitAnswer"]
