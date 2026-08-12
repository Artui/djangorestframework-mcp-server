from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments


@dataclass(frozen=True)
class SelectorDefaults:
    """Per-kind defaults for :func:`register_tools` over selector definitions.

    Sister of :class:`ServiceDefaults`, with the same convention: ``None`` is
    "no override", deferring to the per-definition value or to
    :meth:`MCPServer.register_selector_tool`'s own default, and a
    per-definition kwarg always wins on conflict.

    The selector-only knobs live here too, so a project wanting every selector
    tool to paginate by default says so once. Filtering is not among them —
    ``filter_set`` is declared on each ``SelectorSpec``, never as a
    registration default.
    """

    description: str | None = None
    title: str | None = None
    input_serializer: type | None = None
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    ordering_fields: Sequence[str] | None = None
    paginate: bool | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None


__all__ = ["SelectorDefaults"]
