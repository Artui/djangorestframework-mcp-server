from __future__ import annotations

from collections.abc import Sequence


def is_origin_allowed(origin: str | None, allowed_origins: Sequence[str]) -> bool:
    """Return ``True`` if the request's ``Origin`` header matches the allowlist.

    Per the MCP 2025-11-25 spec a server MUST validate the ``Origin`` header
    on every request to mitigate DNS-rebinding attacks. An empty allowlist
    refuses every cross-origin request, which is the safest default.

    The allowlist is passed in rather than read from settings: it comes from
    the owning server's :class:`MCPConfig`, so two servers in one project can
    accept different origins.

    A missing / empty ``Origin`` header is treated as a same-origin request
    and accepted — this matches browser behaviour where same-origin fetches
    omit ``Origin`` for safe methods.
    """
    if not origin:
        return True
    if "*" in allowed_origins:
        return True
    return origin in allowed_origins


__all__ = ["is_origin_allowed"]
