from __future__ import annotations

import base64

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.handlers.pagination import paginate


def test_no_cursor_starts_at_zero() -> None:
    page, nxt = paginate([1, 2, 3, 4, 5], None, page_size=3)
    assert page == [1, 2, 3]
    assert nxt is not None


def test_cursor_advances_window() -> None:
    page1, c1 = paginate([1, 2, 3, 4, 5], None, page_size=2)
    page2, c2 = paginate([1, 2, 3, 4, 5], c1, page_size=2)
    page3, c3 = paginate([1, 2, 3, 4, 5], c2, page_size=2)
    assert page1 == [1, 2]
    assert page2 == [3, 4]
    assert page3 == [5]
    assert c3 is None


def test_cursor_at_end_returns_none() -> None:
    page, nxt = paginate([1, 2, 3], None, page_size=5)
    assert page == [1, 2, 3]
    assert nxt is None


def test_invalid_base64_cursor_raises() -> None:
    with pytest.raises(ValueError, match="Invalid cursor"):
        paginate([1, 2, 3], cursor="!!!not-base64!!!", page_size=2)


def test_invalid_prefix_cursor_raises() -> None:
    bad = base64.urlsafe_b64encode(b"junk:42").decode().rstrip("=")
    with pytest.raises(ValueError, match="Invalid cursor"):
        paginate([1, 2, 3], cursor=bad, page_size=2)


def test_non_numeric_offset_raises() -> None:
    bad = base64.urlsafe_b64encode(b"offset:nope").decode().rstrip("=")
    with pytest.raises(ValueError, match="Invalid cursor"):
        paginate([1, 2, 3], cursor=bad, page_size=2)


def test_negative_offset_rejected() -> None:
    bad = base64.urlsafe_b64encode(b"offset:-1").decode().rstrip("=")
    with pytest.raises(ValueError, match="non-negative"):
        paginate([1, 2, 3], cursor=bad, page_size=2)


def test_offset_past_end_returns_empty_page() -> None:
    """A hand-crafted offset past the end yields an empty page and no cursor.

    The helper itself never produces such a cursor, but a misbehaving client
    could echo back a stale one — the handler should still degrade gracefully.
    """
    far_cursor = base64.urlsafe_b64encode(b"offset:99").decode().rstrip("=")
    page, nxt = paginate([1, 2], far_cursor, page_size=2)
    assert page == []
    assert nxt is None


def test_page_size_below_one_is_a_configuration_error() -> None:
    """A non-positive ``PAGE_SIZE`` cannot page: it is refused, not served.

    With a page size of 0 every page is empty, so ``next_offset`` equals the
    offset it was handed and ``nextCursor`` re-encodes it for as long as the
    registry is non-empty — a conformant client following ``nextCursor`` until
    it disappears never terminates. ``ImproperlyConfigured`` rather than
    ``ValueError`` because the value comes from the deployment, not from the
    client's cursor, and the listing handlers translate a ``ValueError`` into
    ``-32602`` — blaming the caller for the operator's setting.
    """
    with pytest.raises(ImproperlyConfigured, match="PAGE_SIZE"):
        paginate([1, 2, 3], None, page_size=0)


def test_a_negative_page_size_is_refused_too() -> None:
    with pytest.raises(ImproperlyConfigured, match="at least 1"):
        paginate([1, 2, 3], None, page_size=-5)


def test_a_page_size_of_one_still_pages() -> None:
    """The smallest value that terminates is allowed, and does terminate."""
    seen: list[int] = []
    cursor: str | None = None
    for _ in range(4):  # one more iteration than there are items
        page, cursor = paginate([1, 2, 3], cursor, page_size=1)
        seen.extend(page)
        if cursor is None:
            break
    assert seen == [1, 2, 3]
    assert cursor is None
