from __future__ import annotations

from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.transport.protocol_version import resolve_protocol_version


def negotiate_protocol_version(
    header_value: str | None, *, is_sessionless: bool, config: MCPConfig
) -> str | None:
    """Pick the protocol version to associate with a request, or ``None`` to reject.

    - Supported header -> that version.
    - Sessionless request without a header -> the legacy fallback. The spec
      lets ``initialize`` omit the header, and ``server/discover`` needs the
      same latitude: a client asking which versions a server supports cannot
      be required to name one first.
    - Missing header elsewhere, with
      ``config.require_protocol_version_header`` off -> the legacy fallback.
    - Otherwise ``None``, which callers translate into HTTP 400. A header
      naming a version **this server does not support at all** is rejected on
      every path, sessionless ones included: silently downgrading would mask a
      real version mismatch, and ``initialize`` echoing back a version the
      client never asked for is a diagnostic the client has no other way to
      get.

    A header naming a *modern* version is a different thing and keeps the
    fallback. The server does support it — just not through this handshake — so
    the era check inside ``initialize``, which can say so in words, is where
    that belongs.

    **A modern-only server has no legacy fallback.** Sessionless requests still
    resolve, to the head of the configured list, because discovery must keep
    working or the server is undiscoverable by the clients it still serves;
    ``initialize`` does its own era check, where the message can say so. Any
    other header-less request is a legacy client mid-session and gets ``400``,
    since a modern version would tell it to speak a revision it cannot.
    """
    # **Legacy versions only.** This function serves the handshake era; the
    # modern path validates its version out of the request's own ``_meta``.
    # Accepting a modern version here would let a client claim ``2026-07-28``
    # in a header while sending a legacy body — a request neither era
    # describes.
    supported: tuple[str, ...] = config.legacy_protocol_versions
    resolved: str | None = resolve_protocol_version(header_value, supported)
    if resolved is not None:
        return resolved
    if header_value and header_value not in config.protocol_versions:
        # Unsupported by this server in either era, so there is no version to
        # fall back *to* that the client would recognise. Checked against the
        # whole list rather than the legacy half so a modern version does not
        # read as a typo.
        return None
    fallback: str | None = config.legacy_fallback_version
    if is_sessionless:
        return fallback if fallback is not None else config.protocol_versions[0]
    if not header_value and not config.require_protocol_version_header:
        return fallback
    return None


__all__ = ["negotiate_protocol_version"]
