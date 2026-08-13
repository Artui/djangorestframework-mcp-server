from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments


@dataclass(frozen=True)
class ServiceDefaults:
    """Per-kind defaults for [`register_tools`][rest_framework_mcp.registry.register_tools.register_tools] over service definitions.

    ``None`` is the "no override" sentinel: only non-``None`` values are
    applied as defaults to the matching [`MCPServer.register_service_tool`][rest_framework_mcp.server.mcp_server.MCPServer.register_service_tool]
    call, and a per-definition value always wins.

    That includes the tri-state fields, where ``None`` on the registration
    method means "inherit the global setting": passing
    ``include_structured_content=None`` here is "no override", not a request to
    inherit. Leave it unset for the global, or pass ``True`` / ``False``.
    """

    description: str | None = None
    title: str | None = None
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None


__all__ = ["ServiceDefaults"]
