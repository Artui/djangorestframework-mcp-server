from __future__ import annotations

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.transport.protocol_version import resolve_protocol_version


def negotiate_protocol_version(header_value: str | None, *, is_initialize: bool) -> str | None:
    """Pick the protocol version to associate with a request, or ``None`` to reject.

    - Supported header → that version.
    - ``initialize`` request without a header → the first supported version
      (the spec allows ``initialize`` to omit the header).
    - Missing header on a non-initialize request when
      ``REQUIRE_PROTOCOL_VERSION_HEADER`` is False → the first supported
      version. This exists for clients that omit the header entirely.
    - Otherwise (unsupported version, or missing header with the setting on)
      → ``None``; callers translate that into HTTP 400.

    A *present-but-unsupported* header is always rejected, regardless of the
    setting — silently downgrading would mask a real version mismatch.
    """
    resolved: str | None = resolve_protocol_version(header_value)
    if resolved is not None:
        return resolved
    if is_initialize:
        return list(get_setting("PROTOCOL_VERSIONS"))[0]
    if not header_value and not get_setting("REQUIRE_PROTOCOL_VERSION_HEADER"):
        return list(get_setting("PROTOCOL_VERSIONS"))[0]
    return None


__all__ = ["negotiate_protocol_version"]
