from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import UIVisibility


@dataclass(frozen=True)
class UIToolMeta:
    """Links a tool to the interactive view that renders its result.

    Serialises into the tool's ``_meta`` under the Apps extension's key, so a
    host reading ``tools/list`` knows which ``ui://`` resource to fetch and
    which surfaces may call the tool. The view renders from the tool's
    ``structuredContent``, so a linked tool must emit it:
    :meth:`~rest_framework_mcp.MCPServer.register_service_tool` and friends
    refuse a link when it is switched off.

    Attributes:
        resource_uri: The ``ui://`` URI of a view registered on this same
            server with ``register_ui_resource``. Concrete, never a template —
            the spec defines no expansion mechanism, because the host fetches a
            view once and then pushes each result into it by notification.
        visibility: Who may call the tool. Empty is "unsaid", which hosts read
            as the ordinary model-callable default. **Host-enforced**: this
            server declares it and does not filter ``tools/list`` on it.
    """

    resource_uri: str
    visibility: Sequence[UIVisibility] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the extension's camelCase wire shape, omitting empties."""
        # Serialisation boundary (rule 11): `_meta` is a plain JSON object and
        # every extension key in it is free-form.
        out: dict[str, Any] = {"resourceUri": self.resource_uri}
        if self.visibility:
            out["visibility"] = [v.value for v in self.visibility]
        return out


__all__ = ["UIToolMeta"]
