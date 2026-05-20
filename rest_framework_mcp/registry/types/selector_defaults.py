from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments


@dataclass(frozen=True)
class SelectorDefaults:
    """Per-kind defaults for :func:`register_tools` over selector definitions.

    Sister of :class:`ServiceDefaults`. Same conventions:

    - Every field is ``Optional``.
    - ``None`` = "no override; use the per-definition or the
      :meth:`MCPServer.register_selector_tool` default".
    - Per-definition kwargs always win on conflict.

    Selector-only knobs (``input_serializer``, ``filter_set``,
    ``ordering_fields``, ``paginate``) live here too so a project that
    wants every selector tool to paginate by default can express that
    in one place.
    """

    description: str | None = None
    title: str | None = None
    input_serializer: type | None = None
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    filter_set: Any | None = None
    ordering_fields: Sequence[str] | None = None
    paginate: bool | None = None
    include_structured_content: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None


__all__ = ["SelectorDefaults"]
