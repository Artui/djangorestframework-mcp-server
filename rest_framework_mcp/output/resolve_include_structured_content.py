from __future__ import annotations

from rest_framework_mcp.conf import get_setting


def resolve_include_structured_content(binding_override: bool | None) -> bool:
    """Collapse a binding's tri-state override against the global setting.

    Bindings carry ``include_structured_content: bool | None`` — ``None``
    means "follow the project default", ``True`` / ``False`` are explicit
    overrides. The project default lives in
    ``REST_FRAMEWORK_MCP["INCLUDE_STRUCTURED_CONTENT"]``.
    """
    if binding_override is not None:
        return binding_override
    return bool(get_setting("INCLUDE_STRUCTURED_CONTENT"))


__all__ = ["resolve_include_structured_content"]
