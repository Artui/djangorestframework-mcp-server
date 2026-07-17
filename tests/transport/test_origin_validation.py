from __future__ import annotations

from rest_framework_mcp.transport.origin_validation import is_origin_allowed


def test_missing_origin_is_allowed() -> None:
    assert is_origin_allowed(None, []) is True
    assert is_origin_allowed("", []) is True


def test_wildcard_in_allowlist() -> None:
    assert is_origin_allowed("https://anywhere.example.com", ["*"]) is True


def test_exact_match() -> None:
    allowed = ["https://app.example.com"]
    assert is_origin_allowed("https://app.example.com", allowed) is True
    assert is_origin_allowed("https://other.example.com", allowed) is False


def test_empty_allowlist_rejects_origins() -> None:
    """The spec-mandated safe default: nothing configured, nothing cross-origin."""
    assert is_origin_allowed("https://x", []) is False


def test_two_servers_can_allow_different_origins() -> None:
    """The allowlist arrives as a value rather than a global read — which is the
    point: it comes from each server's own config."""
    assert is_origin_allowed("https://internal.example", ["https://internal.example"]) is True
    assert is_origin_allowed("https://internal.example", ["https://public.example"]) is False
