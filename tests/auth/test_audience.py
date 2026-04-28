from __future__ import annotations

from rest_framework_mcp.auth.audience import audience_matches


def test_unset_expected_disables_enforcement() -> None:
    """When ``RESOURCE_URL`` is not configured, audience is not enforced."""
    assert audience_matches(None, None) is True
    assert audience_matches("https://anything", None) is True


def test_token_without_audience_rejected_when_expected_set() -> None:
    """A token with no ``aud`` claim cannot satisfy a configured resource."""
    assert audience_matches(None, "https://example.com/mcp/") is False


def test_exact_match() -> None:
    assert audience_matches("https://example.com/mcp/", "https://example.com/mcp/") is True


def test_mismatch_rejected() -> None:
    assert audience_matches("https://other.example/mcp/", "https://example.com/mcp/") is False


def test_no_partial_match_for_safety() -> None:
    """Token audiences are URLs, not patterns — substring matches must not pass."""
    assert audience_matches("https://example.com/mcp", "https://example.com/mcp/") is False
    assert audience_matches("https://example.com/mcp/x", "https://example.com/mcp/") is False
