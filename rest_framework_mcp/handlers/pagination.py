from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured

# Cursor scheme: base64url-encoded ``offset:N``. Opaque to clients, which the
# spec requires, but debuggable; the prefix leaves room to reject a cursor
# crafted for a different list endpoint without changing the wire format.
_CURSOR_PREFIX: str = "offset:"


def _encode_cursor(offset: int) -> str:
    raw: bytes = f"{_CURSOR_PREFIX}{offset}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    """Parse an opaque cursor back into a numeric offset.

    Raises ``ValueError`` on any malformed input so callers can map to
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
    *,
    page_size: int,
) -> tuple[list[Any], str | None]:
    """Slice ``items`` into a page of at most ``page_size``, starting at ``cursor``.

    Returns ``(page, next_cursor)``; ``next_cursor`` is ``None`` at the end of
    the sequence, the spec's signal that no more pages are available. A
    malformed cursor raises ``ValueError`` for the caller to translate.

    A ``page_size`` below 1 raises ``ImproperlyConfigured``, which is a
    *different* failure from a bad cursor and deliberately not a ``ValueError``:
    the cursor comes from the client and maps to ``-32602``, while this can only
    come from the deployment's ``PAGE_SIZE``. Left to run it does not merely
    serve an empty page — every page is empty, so ``next_cursor`` re-encodes the
    offset it was handed for as long as the registry is non-empty and a
    conformant client following ``nextCursor`` never terminates.
    """
    if page_size < 1:
        raise ImproperlyConfigured(
            f"REST_FRAMEWORK_MCP['PAGE_SIZE'] is {page_size}, so no listing can make "
            "progress: every page would be empty and its nextCursor would point at "
            "the same offset forever. Set it to at least 1."
        )
    offset: int = _decode_cursor(cursor) if cursor else 0
    if offset < 0:
        raise ValueError(f"Cursor offset must be non-negative: {offset}")
    page: list[Any] = list(items[offset : offset + page_size])
    next_offset: int = offset + len(page)
    next_cursor: str | None = _encode_cursor(next_offset) if next_offset < len(items) else None
    return page, next_cursor


__all__ = ["paginate"]
