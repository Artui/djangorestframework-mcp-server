from __future__ import annotations

from collections.abc import Sequence


def resolve_protocol_version(header_value: str | None, supported: Sequence[str]) -> str | None:
    """Validate the ``MCP-Protocol-Version`` header against ``supported``.

    Returns the version when supported, ``None`` when it is not or when the
    header is missing or empty. Deciding what absence means belongs to the
    caller: a missing header is allowed only on ``initialize``.
    """
    if not header_value:
        return None
    if header_value not in supported:
        return None
    return header_value


__all__ = ["resolve_protocol_version"]
