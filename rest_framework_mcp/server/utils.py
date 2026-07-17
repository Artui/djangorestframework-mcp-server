"""Internal helpers shared by :class:`MCPServer`'s registration methods."""

from __future__ import annotations

import warnings
from typing import Any

from django.core.exceptions import ImproperlyConfigured


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


__all__ = ["UnguardedToolWarning", "check_tool_permissions_declared"]
