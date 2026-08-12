from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Completion:
    """The ``completion`` object inside a ``completion/complete`` result.

    Attributes:
        values: Suggestions ranked by relevance, capped at
            ``MAX_COMPLETION_VALUES`` by the spec.
        total: Count of all matches. Optional, and left unset by this package:
            knowing it would mean counting every match, which is the work a
            queryset-backed completer should be allowed to skip.
        has_more: Whether the completer had more to give.
    """

    values: tuple[str, ...] = ()
    total: int | None = None
    has_more: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"values": list(self.values), "hasMore": self.has_more}
        if self.total is not None:
            out["total"] = self.total
        return out


__all__ = ["Completion"]
