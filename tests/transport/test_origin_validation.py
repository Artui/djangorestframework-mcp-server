from __future__ import annotations

from rest_framework_mcp.transport.origin_validation import is_origin_allowed


def test_missing_origin_is_allowed() -> None:
    assert is_origin_allowed(None) is True
    assert is_origin_allowed("") is True


def test_wildcard_in_allowlist(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"ALLOWED_ORIGINS": ["*"]}
    assert is_origin_allowed("https://anywhere.example.com") is True


def test_exact_match(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"ALLOWED_ORIGINS": ["https://app.example.com"]}
    assert is_origin_allowed("https://app.example.com") is True
    assert is_origin_allowed("https://other.example.com") is False


def test_empty_allowlist_rejects_origins(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"ALLOWED_ORIGINS": []}
    assert is_origin_allowed("https://x") is False
