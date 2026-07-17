from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Implementation:
    """Identifies an MCP client or server: name + version, with an optional title.

    Mirrors the spec's ``Implementation extends BaseMetadata``. The two labels
    are **not** interchangeable, and the split is the spec's own:

    - :attr:`name` is *"intended for programmatic or logical use"* — the stable
      identifier. This is what distinguishes two servers to a client, and what
      server-scoped state keys off.
    - :attr:`title` is *"intended for UI and end-user contexts"* — the
      human-readable label. Optional; clients fall back to ``name`` when absent.
    """

    name: str
    version: str
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.title is not None:
            out["title"] = self.title
        return out


__all__ = ["Implementation"]
