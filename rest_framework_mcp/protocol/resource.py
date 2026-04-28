from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Resource:
    """A concrete MCP resource as returned by ``resources/list``.

    ``uri`` is the canonical address (e.g. ``"invoices://1"``); ``mime_type``
    advertises how the contents will be encoded by ``resources/read``.
    """

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    size: int | None = None
    title: str | None = None
    annotations: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"uri": self.uri, "name": self.name}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.size is not None:
            out["size"] = self.size
        if self.annotations is not None:
            out["annotations"] = self.annotations
        return out


__all__ = ["Resource"]
