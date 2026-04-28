from __future__ import annotations

from rest_framework_mcp.conf import get_setting


def resolve_protocol_version(header_value: str | None) -> str | None:
    """Validate the ``MCP-Protocol-Version`` header.

    Returns the version when supported, ``None`` when it is not. Callers
    translate ``None`` into a 400 response. A missing header is allowed only
    on the ``initialize`` request — that branching belongs in the view, so
    we treat absence and presence-but-unsupported as separate cases here:
    ``""`` → ``None`` (so a stripped/empty header behaves like missing on
    the negative path), ``None`` → ``None``.
    """
    if not header_value:
        return None
    supported: list[str] = list(get_setting("PROTOCOL_VERSIONS"))
    if header_value not in supported:
        return None
    return header_value


__all__ = ["resolve_protocol_version"]
