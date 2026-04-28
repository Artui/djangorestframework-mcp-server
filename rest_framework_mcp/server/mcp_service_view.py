from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework.request import Request


@dataclass(frozen=True)
class MCPServiceView:
    """Minimal :class:`rest_framework_services.ServiceView` adapter for MCP.

    Per-spec ``kwargs`` providers on ``ServiceSpec`` / ``SelectorSpec`` (added
    in djangorestframework-services 0.6) are typed against
    :class:`~rest_framework_services.ServiceView` — a structural Protocol
    requiring ``request``, ``kwargs``, and ``action``. The MCP transport
    doesn't go through DRF views, so we synthesise an instance that satisfies
    the Protocol and pass it into the provider.

    - ``request`` is the internal DRF Request the dispatch flow already
      builds (so providers can read ``request.user`` etc.).
    - ``kwargs`` is the URI-template variables on resource reads, or an empty
      dict on tool calls.
    - ``action`` is the binding name (``"invoices.create"``, etc.) — gives
      providers a stable identifier without digging at view internals.
    """

    request: Request
    action: str | None
    kwargs: dict[str, Any] = field(default_factory=dict)


__all__ = ["MCPServiceView"]
