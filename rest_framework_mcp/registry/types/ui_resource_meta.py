from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import UIPermission
from rest_framework_mcp.registry.types.ui_csp import UICsp


@dataclass(frozen=True)
class UIResourceMeta:
    """What a host needs to know to render an interactive view.

    Serialises into the resource's ``_meta`` under the Apps extension's key.
    Typed at the registration parameter rather than in the wire types, because
    ``_meta`` is an open namespace shared by every extension: it stays a
    free-form dict at the boundary while each extension keeps its own closed
    shape on the way in.

    Attributes:
        csp: Origins the view needs; see :class:`UICsp`.
        permissions: Browser capabilities the view would use. The host decides
            whether to grant them.
        domain: A stable identity for the view's origin, letting a host group
            views from one publisher — for a single consent prompt, say —
            rather than treating every URI as unrelated.
        prefers_border: Rendering hint that the view looks better with the
            host's chrome around it. A hint, not a requirement.
    """

    csp: UICsp | None = None
    permissions: Sequence[UIPermission] = ()
    domain: str | None = None
    prefers_border: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the extension's camelCase wire shape, omitting empties."""
        # Serialisation boundary (rule 11) — see `UICsp.to_dict`.
        out: dict[str, Any] = {}
        if self.csp is not None:
            csp = self.csp.to_dict()
            if csp:
                out["csp"] = csp
        if self.permissions:
            out["permissions"] = [p.value for p in self.permissions]
        if self.domain is not None:
            out["domain"] = self.domain
        if self.prefers_border is not None:
            out["prefersBorder"] = self.prefers_border
        return out


__all__ = ["UIResourceMeta"]
