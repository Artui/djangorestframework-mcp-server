from __future__ import annotations

from rest_framework_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
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


def test_negotiate_supported_passthrough(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PROTOCOL_VERSIONS": ["2025-11-25"]}
    assert negotiate_protocol_version("2025-11-25", is_initialize=False) == "2025-11-25"


def test_negotiate_initialize_missing_header_uses_default(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PROTOCOL_VERSIONS": ["2025-11-25", "2025-06-18"]}
    assert negotiate_protocol_version(None, is_initialize=True) == "2025-11-25"


def test_negotiate_initialize_unsupported_header_uses_default(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PROTOCOL_VERSIONS": ["2025-11-25"]}
    assert negotiate_protocol_version("9999-99-99", is_initialize=True) == "2025-11-25"


def test_negotiate_non_initialize_missing_header_rejected_by_default(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {"PROTOCOL_VERSIONS": ["2025-11-25"]}
    assert negotiate_protocol_version(None, is_initialize=False) is None


def test_negotiate_non_initialize_missing_header_allowed_when_disabled(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "PROTOCOL_VERSIONS": ["2025-11-25", "2025-06-18"],
        "REQUIRE_PROTOCOL_VERSION_HEADER": False,
    }
    assert negotiate_protocol_version(None, is_initialize=False) == "2025-11-25"
    assert negotiate_protocol_version("", is_initialize=False) == "2025-11-25"


def test_negotiate_unsupported_header_still_rejected_when_disabled(settings) -> None:
    settings.REST_FRAMEWORK_MCP = {
        "PROTOCOL_VERSIONS": ["2025-11-25"],
        "REQUIRE_PROTOCOL_VERSION_HEADER": False,
    }
    # A present-but-unsupported header is a real mismatch; never silently downgrade.
    assert negotiate_protocol_version("9999-99-99", is_initialize=False) is None
