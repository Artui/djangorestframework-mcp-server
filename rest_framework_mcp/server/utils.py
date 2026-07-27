"""Internal helpers shared by :class:`MCPServer`'s registration methods."""

from __future__ import annotations

import warnings
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.constants import UI_META_KEY, UI_RESOURCE_MIME_TYPE
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.types.ui_tool_meta import UIToolMeta


class UnguardedToolWarning(UserWarning):
    """A tool was registered with no MCP permissions at all.

    Dedicated category so consumers can silence or escalate it precisely
    via ``warnings.filterwarnings`` without touching other ``UserWarning``
    traffic.
    """


def check_tool_permissions_declared(
    name: str, permissions: tuple[Any, ...], *, require: bool
) -> None:
    """Warn (or raise) when a tool binding carries no permissions.

    ``permissions`` is the binding's *effective* tuple — author-declared
    ``spec.permission_classes`` (wrapped in ``DRFPermissionAdapter``) plus
    any per-binding ``MCPPermission`` instances — so an empty tuple means
    nothing gates the call beyond transport authentication.

    The trap this guards: DRF viewset-level and ``REST_FRAMEWORK`` default
    permission classes do **not** apply over MCP (the package deliberately
    bypasses DRF's view pipeline). A developer who guards a viewset the
    usual way, sees HTTP tests pass, and exposes the same spec over MCP
    would otherwise ship an unguarded tool with no signal.

    Deliberately emits on every unguarded registration (no warn-once
    module state — see the repo's no-module-level-mutable-state rule);
    registration happens once per server instance, so the volume is one
    warning per unguarded tool. ``require`` (the server's
    ``MCPConfig.require_tool_permissions``) refuses the registration
    outright instead — so one server in a project can demand guarded tools
    while another only warns.
    """
    if permissions:
        return
    message = (
        f"MCP tool {name!r} is registered with no permissions: neither "
        "spec.permission_classes nor a per-binding permissions=[...] is set. "
        "DRF viewset-level and REST_FRAMEWORK default permission classes do "
        "NOT apply over MCP, so this tool is callable by any principal the "
        "transport authenticates. Set spec.permission_classes, pass "
        "permissions=[...] at registration, or set "
        "REST_FRAMEWORK_MCP['REQUIRE_TOOL_PERMISSIONS'] = True to make this "
        "an error."
    )
    if require:
        raise ImproperlyConfigured(message)
    warnings.warn(message, UnguardedToolWarning, stacklevel=3)


def build_ui_tool_meta(
    *,
    name: str,
    ui: UIToolMeta | None,
    meta: dict[str, Any] | None,
    resources: ResourceRegistry,
    include_structured_content: bool | None,
    default_structured_content: bool,
) -> dict[str, Any]:
    """Validate a tool's view link and return its ``_meta`` contribution.

    Returns an empty dict when the tool declares no link, so the caller can
    hand the result to ``merge_meta`` unconditionally.

    Three ways a link can be wrong, all of which fail the same way at runtime —
    a view that silently never renders — and all of which are therefore raised
    here, at registration:

    1. **Both ``ui=`` and a ``"ui"`` key in ``meta=``.** They write the same
       ``_meta`` key, so one would quietly overwrite the other.
    2. **``resource_uri`` doesn't name a view on this server.** The host reads
       the view from the same server it read the tool from, so the URI has to
       resolve here — a typo would otherwise reach the host as a dangling
       reference. This means a view must be registered *before* the tool that
       links to it.
    3. **The tool doesn't emit ``structuredContent``.** That *is* the render
       payload a view consumes, so a linked tool with it switched off starves
       its own view. Checked against the effective value — the per-binding
       override if given, otherwise the server's configured default — which
       also catches a project that turned it off globally.
    """
    if ui is None:
        return {}
    if meta is not None and UI_META_KEY in meta:
        raise ValueError(
            f"Tool {name!r} got both ui= and a {UI_META_KEY!r} key in meta=. Both "
            "write the same _meta key, so one would silently win. Pass the typed "
            "ui= alone, or drop it and hand-write the whole bundle in meta=."
        )

    resolved = resources.resolve(ui.resource_uri)
    if resolved is None or resolved[0].mime_type != UI_RESOURCE_MIME_TYPE:
        raise ValueError(
            f"Tool {name!r} links to {ui.resource_uri!r}, which is not a view "
            "registered on this server. Register it with register_ui_resource() "
            "first — a host resolves the URI against this same server, so a link "
            "it cannot resolve renders nothing and reports nothing."
        )

    emits_structured = (
        include_structured_content
        if include_structured_content is not None
        else default_structured_content
    )
    if not emits_structured:
        raise ValueError(
            f"Tool {name!r} links to a view but does not emit structuredContent, "
            "which is the payload the view renders from — so the view would come "
            "up blank. Set include_structured_content=True on this registration"
            + (
                ""
                if include_structured_content is not None
                else " (it is off by default for this server — see "
                "REST_FRAMEWORK_MCP['INCLUDE_STRUCTURED_CONTENT'])"
            )
            + ", or drop the ui= link."
        )

    return {UI_META_KEY: ui.to_dict()}


__all__ = [
    "UnguardedToolWarning",
    "build_ui_tool_meta",
    "check_tool_permissions_declared",
]
