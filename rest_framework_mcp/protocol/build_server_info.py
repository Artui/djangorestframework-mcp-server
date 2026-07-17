from __future__ import annotations

from typing import Any

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.version import __version__ as package_version


def build_server_info(name: str | None = None, version: str | None = None) -> Implementation:
    """Resolve a server's wire identity, falling back to the ``SERVER_INFO`` setting.

    Called once per server from :meth:`MCPServer.__init__`, so the settings read
    happens at construction rather than on every ``initialize`` — the instance is
    then the single source of truth and two servers mounted in one project answer
    with their own names.

    Either field may be ``None`` to take that value from ``SERVER_INFO`` (and,
    failing that, the package defaults), so a project that configures
    ``SERVER_INFO`` and never passes ``name=`` keeps its current wire identity.

    Lives here rather than beside ``handle_initialize`` because ``MCPServer``
    also needs it, and ``server`` already imports ``handlers`` — the other
    direction would cycle.
    """
    server_info_settings: dict[str, Any] = get_setting("SERVER_INFO")
    return Implementation(
        name=name
        if name is not None
        else server_info_settings.get("name", "djangorestframework-mcp-server"),
        version=version
        if version is not None
        else server_info_settings.get("version", package_version),
    )


__all__ = ["build_server_info"]
