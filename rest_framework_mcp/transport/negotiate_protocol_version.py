from __future__ import annotations

from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.transport.protocol_version import resolve_protocol_version


def negotiate_protocol_version(
    header_value: str | None, *, is_initialize: bool, config: MCPConfig
) -> str | None:
    """Pick the protocol version to associate with a request, or ``None`` to reject.

    - Supported header -> that version.
    - ``initialize`` request without a header -> the first supported version
      (the spec allows ``initialize`` to omit the header).
    - Missing header on a non-initialize request when
      ``config.require_protocol_version_header`` is False -> the first
      supported version. This exists for clients that omit the header entirely.
    - Otherwise (unsupported version, or missing header with the flag on)
      -> ``None``; callers translate that into HTTP 400.

    A *present-but-unsupported* header is always rejected, regardless of the
    flag — silently downgrading would mask a real version mismatch.
    """
    resolved: str | None = resolve_protocol_version(header_value, config.protocol_versions)
    if resolved is not None:
        return resolved
    if is_initialize:
        return config.protocol_versions[0]
    if not header_value and not config.require_protocol_version_header:
        return config.protocol_versions[0]
    return None


__all__ = ["negotiate_protocol_version"]
