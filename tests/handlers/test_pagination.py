from __future__ import annotations

import base64

import pytest

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
