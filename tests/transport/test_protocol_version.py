from __future__ import annotations

from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from rest_framework_mcp.transport.protocol_version import resolve_protocol_version


def _config(*, versions: list[str], require_header: bool = True) -> MCPConfig:
    """A server's resolved config. Built from values, not settings: negotiation
    reads the owning server's config, so two servers could support different
    protocol versions."""
    return build_mcp_config(
        protocol_versions=versions,
        require_protocol_version_header=require_header,
    )


def test_none_header_returns_none() -> None:
    assert resolve_protocol_version(None, ["2025-11-25"]) is None


def test_empty_string_returns_none() -> None:
    assert resolve_protocol_version("", ["2025-11-25"]) is None


def test_unsupported_version_returns_none() -> None:
    assert resolve_protocol_version("1999-01-01", ["2025-11-25"]) is None


def test_supported_version_passthrough() -> None:
    assert resolve_protocol_version("2025-11-25", ["2025-11-25"]) == "2025-11-25"


def test_negotiate_supported_passthrough() -> None:
    config = _config(versions=["2025-11-25"])
    assert negotiate_protocol_version("2025-11-25", is_initialize=False, config=config) == (
        "2025-11-25"
    )


def test_negotiate_initialize_missing_header_uses_default() -> None:
    config = _config(versions=["2025-11-25", "2025-06-18"])
    assert negotiate_protocol_version(None, is_initialize=True, config=config) == "2025-11-25"


def test_negotiate_initialize_unsupported_header_uses_default() -> None:
    config = _config(versions=["2025-11-25"])
    assert negotiate_protocol_version("9999-99-99", is_initialize=True, config=config) == (
        "2025-11-25"
    )


def test_negotiate_non_initialize_missing_header_rejected_by_default() -> None:
    config = _config(versions=["2025-11-25"])
    assert negotiate_protocol_version(None, is_initialize=False, config=config) is None


def test_negotiate_non_initialize_missing_header_allowed_when_disabled() -> None:
    config = _config(versions=["2025-11-25", "2025-06-18"], require_header=False)
    assert negotiate_protocol_version(None, is_initialize=False, config=config) == "2025-11-25"
    assert negotiate_protocol_version("", is_initialize=False, config=config) == "2025-11-25"


def test_negotiate_unsupported_header_still_rejected_when_disabled() -> None:
    config = _config(versions=["2025-11-25"], require_header=False)
    # A present-but-unsupported header is a real mismatch; never silently downgrade.
    assert negotiate_protocol_version("9999-99-99", is_initialize=False, config=config) is None
