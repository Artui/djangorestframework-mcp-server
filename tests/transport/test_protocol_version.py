from __future__ import annotations

from rest_framework_mcp.transport.protocol_version import resolve_protocol_version


def test_none_header_returns_none() -> None:
    assert resolve_protocol_version(None) is None


def test_empty_string_returns_none() -> None:
    assert resolve_protocol_version("") is None


def test_unsupported_version_returns_none(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PROTOCOL_VERSIONS": ["2025-11-25"]}
    assert resolve_protocol_version("1999-01-01") is None


def test_supported_version_passthrough(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PROTOCOL_VERSIONS": ["2025-11-25"]}
    assert resolve_protocol_version("2025-11-25") == "2025-11-25"
