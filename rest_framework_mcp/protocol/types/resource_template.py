from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResourceTemplate:
    """A parameterised resource address (RFC 6570 URI Template).

    Returned by ``resources/templates/list``. Clients fill in template
    variables and call ``resources/read`` with the resulting URI.
    """

    uri_template: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    title: str | None = None
    annotations: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"uriTemplate": self.uri_template, "name": self.name}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.annotations is not None:
            out["annotations"] = self.annotations
        return out


__all__ = ["ResourceTemplate"]
