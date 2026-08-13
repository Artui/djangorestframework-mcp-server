from __future__ import annotations

from typing import Any

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import IconTheme
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.version import __version__ as package_version


# Lives here rather than beside ``handle_initialize`` because ``MCPServer`` needs
# it too and ``server`` already imports ``handlers``; the other direction would
# cycle.
def build_server_info(
    name: str | None = None,
    version: str | None = None,
    title: str | None = None,
    website_url: str | None = None,
    icons: tuple[Icon, ...] | None = None,
) -> Implementation:
    """Resolve a server's wire identity, falling back to the ``SERVER_INFO`` setting.

    Called once per server from ``MCPServer.__init__``, so the settings read
    happens at construction rather than on every ``initialize`` and two servers
    mounted in one project answer with their own names.

    Any field may be ``None`` to take that value from ``SERVER_INFO`` and, failing
    that, the package defaults. ``title`` / ``website_url`` / ``icons`` have no
    default — absent means absent, and the client falls back to ``name`` per the
    spec.

    ``description`` is deliberately settings-only, with no parameter here:
    ``MCPServer`` already spends the name ``description=`` on the ``initialize``
    ``instructions`` string. [`Implementation`][rest_framework_mcp.protocol.types.implementation.Implementation] documents the distinction.

    ``icons`` arriving from ``SERVER_INFO`` are plain data, so dicts are accepted
    alongside [`Icon`][rest_framework_mcp.protocol.types.icon.Icon] instances and normalised here — ``Icon``'s scheme
    validation runs whichever form the project used.
    """
    server_info_settings: dict[str, Any] = get_setting("SERVER_INFO")
    return Implementation(
        name=name
        if name is not None
        else server_info_settings.get("name", "djangorestframework-mcp-server"),
        version=version
        if version is not None
        else server_info_settings.get("version", package_version),
        title=title if title is not None else server_info_settings.get("title"),
        description=server_info_settings.get("description"),
        website_url=website_url
        if website_url is not None
        else server_info_settings.get("websiteUrl"),
        icons=icons if icons is not None else _coerce_icons(server_info_settings.get("icons")),
    )


def _coerce_icons(raw: Any) -> tuple[Icon, ...]:
    """Normalise the ``SERVER_INFO`` representation of ``icons`` into [`Icon`][rest_framework_mcp.protocol.types.icon.Icon]s."""
    if not raw:
        return ()
    return tuple(item if isinstance(item, Icon) else _icon_from_mapping(item) for item in raw)


def _icon_from_mapping(item: Any) -> Icon:
    """Build an [`Icon`][rest_framework_mcp.protocol.types.icon.Icon] from settings data, which uses the wire spellings.

    Fields are read by name rather than splatted so ``mimeType`` maps to
    ``mime_type``, and so a stray settings key is an ignored extra rather than a
    confusing ``TypeError`` from the dataclass initialiser.
    """
    theme: Any = item.get("theme")
    return Icon(
        src=item["src"],
        mime_type=item.get("mimeType"),
        sizes=tuple(item.get("sizes", ())),
        theme=IconTheme(theme) if theme else None,
    )


__all__ = ["build_server_info"]
