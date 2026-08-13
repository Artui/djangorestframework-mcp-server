from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.constants import ElicitAction


@dataclass(frozen=True)
class ResolvedInput:
    """What a request looks like once any earlier round's answers are folded in.

    Produced once per dispatch, before the service runs, and read again if the
    service asks for more — so it carries both the merged arguments and the
    bookkeeping the next question needs.
    """

    arguments: dict[str, Any]
    """The client's arguments with every accepted answer merged over them.
    Answers win on a name collision — the later, human-confirmed value."""

    fingerprint: str
    """Identity of this call, for binding the next round's state to it."""

    round: int = 0
    """Rounds already completed. ``0`` on a first attempt."""

    refused_with: ElicitAction | None = None
    """Set when the client came back with a ``decline`` or a ``cancel``.

    **Not an error, and not a reason to ask again.** The dispatch stops here and
    the caller gets a tool error naming which of the two it was."""

    carried: dict[str, Any] = field(default_factory=dict)
    """The accumulated answers on their own, ready to be re-signed into the next
    round's state. Kept separate from ``arguments`` because the state must
    carry only what the *user* supplied, never the client's original
    arguments."""


__all__ = ["ResolvedInput"]
