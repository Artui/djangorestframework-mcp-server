from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Implementation:
    """Identifies an MCP client or server: name + version."""

    name: str
    version: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}


__all__ = ["Implementation"]
