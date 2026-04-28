from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContentBlock:
    """A single content block in a :class:`ToolResult`.

    ``type`` is normally ``"text"``; ``"image"`` and ``"resource"`` are
    reserved for future use. The encoder writes only ``"text"`` blocks today.
    """

    type: str
    text: str | None = None
    data: str | None = None
    mime_type: str | None = None
    annotations: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.text is not None:
            out["text"] = self.text
        if self.data is not None:
            out["data"] = self.data
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.annotations is not None:
            out["annotations"] = self.annotations
        return out


__all__ = ["ToolContentBlock"]
