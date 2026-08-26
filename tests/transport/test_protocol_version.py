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
    assert negotiate_protocol_version("2025-11-25", is_sessionless=False, config=config) == (
        "2025-11-25"
    )


def test_negotiate_initialize_missing_header_uses_default() -> None:
    config = _config(versions=["2025-11-25", "2025-06-18"])
    assert negotiate_protocol_version(None, is_sessionless=True, config=config) == "2025-11-25"


def test_negotiate_initialize_unsupported_header_is_rejected() -> None:
    """A sessionless method is latitude about an *absent* header, not licence to
    downgrade a header that named a version this server does not speak. The
    client asked for something specific and would otherwise be answered with
    something else, with nothing saying so."""
    config = _config(versions=["2025-11-25"])
    assert negotiate_protocol_version("9999-99-99", is_sessionless=True, config=config) is None


def test_negotiate_initialize_modern_header_still_uses_the_legacy_default() -> None:
    """Not the same condition: the server *does* support this version, just not
    through the handshake, so the era check inside ``initialize`` — which can
    explain itself — is what should answer it."""
    config = _config(versions=["2026-07-28", "2025-11-25"])
    assert negotiate_protocol_version("2026-07-28", is_sessionless=True, config=config) == (
        "2025-11-25"
    )


def test_negotiate_non_initialize_missing_header_rejected_by_default() -> None:
    config = _config(versions=["2025-11-25"])
    assert negotiate_protocol_version(None, is_sessionless=False, config=config) is None


def test_negotiate_non_initialize_missing_header_allowed_when_disabled() -> None:
    config = _config(versions=["2025-11-25", "2025-06-18"], require_header=False)
    assert negotiate_protocol_version(None, is_sessionless=False, config=config) == "2025-11-25"
    assert negotiate_protocol_version("", is_sessionless=False, config=config) == "2025-11-25"


def test_negotiate_unsupported_header_still_rejected_when_disabled() -> None:
    config = _config(versions=["2025-11-25"], require_header=False)
    # A present-but-unsupported header is a real mismatch; never silently downgrade.
    assert negotiate_protocol_version("9999-99-99", is_sessionless=False, config=config) is None
