from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

from rest_framework_mcp.conf import get_setting

# Cursor scheme: base64url-encoded ``offset:N``. Opaque to clients (the spec
# requires they treat it as a black box) but trivially debuggable when needed.
# A custom prefix lets us reject cursors crafted for a different list endpoint
# in the future without changing the wire format.
_CURSOR_PREFIX: str = "offset:"


def _encode_cursor(offset: int) -> str:
    raw: bytes = f"{_CURSOR_PREFIX}{offset}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    """Parse an opaque cursor back into a numeric offset.

    Raises :class:`ValueError` on any malformed input so callers can map to
    JSON-RPC ``-32602``.
    """
    padding: str = "=" * (-len(cursor) % 4)
    try:
        decoded: str = base64.urlsafe_b64decode(cursor + padding).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid cursor: {cursor!r}") from exc
    if not decoded.startswith(_CURSOR_PREFIX):
        raise ValueError(f"Invalid cursor: {cursor!r}")
    try:
        return int(decoded[len(_CURSOR_PREFIX) :])
    except ValueError as exc:
        raise ValueError(f"Invalid cursor: {cursor!r}") from exc


def paginate(
    items: Sequence[Any],
    cursor: str | None,
) -> tuple[list[Any], str | None]:
    """Slice ``items`` per the configured ``PAGE_SIZE`` starting at ``cursor``.

    Returns ``(page, next_cursor)``. ``next_cursor`` is ``None`` when the page
    reaches the end of the sequence — that's the spec-compliant signal that
    no more pages are available.

    The function is pure: callers handle the JSON-RPC error translation when
    ``ValueError`` propagates from a malformed cursor.
    """
    page_size: int = int(get_setting("PAGE_SIZE"))
    offset: int = _decode_cursor(cursor) if cursor else 0
    if offset < 0:
        raise ValueError(f"Cursor offset must be non-negative: {offset}")
    page: list[Any] = list(items[offset : offset + page_size])
    next_offset: int = offset + len(page)
    next_cursor: str | None = _encode_cursor(next_offset) if next_offset < len(items) else None
    return page, next_cursor


__all__ = ["paginate"]
