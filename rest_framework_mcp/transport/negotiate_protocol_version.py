from __future__ import annotations

from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.transport.protocol_version import resolve_protocol_version


def negotiate_protocol_version(
    header_value: str | None, *, is_sessionless: bool, config: MCPConfig
) -> str | None:
    """Pick the protocol version to associate with a request, or ``None`` to reject.

    - Supported header -> that version.
    - A sessionless request without a header -> the first supported version.
      The spec allows ``initialize`` to omit the header, and ``server/discover``
      needs the same latitude: a client asking which versions a server supports
      cannot be required to name one first.
    - Missing header on any other request when
      ``config.require_protocol_version_header`` is False -> the first
      supported version. This exists for clients that omit the header entirely.
    - Otherwise (unsupported version, or missing header with the flag on)
      -> ``None``; callers translate that into HTTP 400.

    A *present-but-unsupported* header is always rejected, regardless of the
    flag — silently downgrading would mask a real version mismatch.
    """
    # ⚠ **Legacy versions only.** This function serves the handshake era; the
    # modern path validates its version out of the request's own ``_meta``
    # (see ``validate_modern_request``). Accepting a modern version here would
    # let a client claim ``2026-07-28`` in a header while sending a legacy
    # body — a request neither era's rules describe.
    supported: tuple[str, ...] = config.legacy_protocol_versions
    resolved: str | None = resolve_protocol_version(header_value, supported)
    if resolved is not None:
        return resolved
    if is_sessionless:
        return supported[0]
    if not header_value and not config.require_protocol_version_header:
        return supported[0]
    return None


__all__ = ["negotiate_protocol_version"]
