from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments


@dataclass(frozen=True)
class ServiceDefaults:
    """Per-kind defaults for :func:`register_tools` over service definitions.

    Every field is ``Optional`` and ``None`` is the "no override"
    sentinel — only non-``None`` values are applied as defaults to the
    matching :meth:`MCPServer.register_service_tool` call. A per-
    definition value always wins over the default.

    Because ``include_structured_content`` and ``include_output_schema``
    are tri-state on the registration method (``None`` = inherit global
    setting, ``True``/``False`` = force), the same ``None``-as-sentinel
    convention applies here: ``ServiceDefaults(include_structured_content=None)``
    is identical to "no override" — if you want to force every binding
    to inherit the global, leave it unset; if you want to force
    ``True``/``False``, pass that explicitly.
    """

    description: str | None = None
    title: str | None = None
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None


__all__ = ["ServiceDefaults"]
