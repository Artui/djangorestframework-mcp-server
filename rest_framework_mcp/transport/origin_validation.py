from __future__ import annotations

from rest_framework_mcp.conf import get_setting


def is_origin_allowed(origin: str | None) -> bool:
    """Return ``True`` if the request's ``Origin`` header matches the allowlist.

    Per the MCP 2025-11-25 spec a server MUST validate the ``Origin`` header
    on every request to mitigate DNS-rebinding attacks. The allowlist is
    settings-configured via ``REST_FRAMEWORK_MCP["ALLOWED_ORIGINS"]``; an
    empty allowlist refuses every cross-origin request, which is the safest
    default.

    A missing / empty ``Origin`` header is treated as a same-origin request
    and accepted — this matches browser behaviour where same-origin fetches
    omit ``Origin`` for safe methods.
    """
    if not origin:
        return True
    allowlist: list[str] = list(get_setting("ALLOWED_ORIGINS"))
    if "*" in allowlist:
        return True
    return origin in allowlist


__all__ = ["is_origin_allowed"]
