from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Completion:
    """The ``completion`` object inside a ``completion/complete`` result.

    ``values`` are suggestions ranked by relevance, capped at
    ``MAX_COMPLETION_VALUES`` by the spec. ``has_more`` says whether the
    completer had more to give; ``total`` is the count of all matches and is
    optional — this package leaves it unset, because knowing it would mean
    counting every match, which is exactly the work a completer built on a
    queryset should be allowed to skip.
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
