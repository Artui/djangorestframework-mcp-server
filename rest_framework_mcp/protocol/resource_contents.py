from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResourceContents:
    """One ``contents`` entry returned by ``resources/read``.

    Either ``text`` or ``blob`` is set — never both. ``blob`` is base64-
    encoded per the MCP spec.
    """

    uri: str
    mime_type: str | None = None
    text: str | None = None
    blob: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"uri": self.uri}
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.text is not None:
            out["text"] = self.text
        if self.blob is not None:
            out["blob"] = self.blob
        return out


__all__ = ["ResourceContents"]
