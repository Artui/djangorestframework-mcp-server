"""Internal helpers shared by
[`MCPServer`][rest_framework_mcp.server.mcp_server.MCPServer]'s registration methods."""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.constants import UI_META_KEY, UI_RESOURCE_MIME_TYPE
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.types.ui_tool_meta import UIToolMeta


class UnguardedToolWarning(UserWarning):
    """A tool was registered with no MCP permissions at all.

    Its own category so consumers can filter it precisely via
    ``warnings.filterwarnings``.
    """


def check_tool_permissions_declared(
    name: str, permissions: tuple[Any, ...], *, require: bool
) -> None:
    """Warn (or raise) when a tool binding carries no permissions.

    ``permissions`` is the binding's *effective* tuple — author-declared
    ``spec.permission_classes`` (wrapped in ``DRFPermissionAdapter``) plus any
    per-binding ``MCPPermission`` — so an empty tuple means nothing gates the
    call beyond transport authentication. The trap: DRF viewset-level and
    ``REST_FRAMEWORK`` default permission classes do **not** apply over MCP,
    so a spec guarded the usual way, with passing HTTP tests, otherwise ships
    as an unguarded tool with no signal.

    Emits on every unguarded registration — no warn-once module state, per the
    repo's no-module-level-mutable-state rule.
    """
    if permissions:
        return
    problem = (
        f"MCP tool {name!r} is registered with no permissions: neither "
        "spec.permission_classes nor a per-binding permissions=[...] is set. "
        "DRF viewset-level and REST_FRAMEWORK default permission classes do "
        "NOT apply over MCP, so this tool is callable by any principal the "
        "transport authenticates. Set spec.permission_classes, or pass "
        "permissions=[...] at registration."
    )
    if require:
        # The remedy on this branch is the opposite of the warning's: the check
        # is already strict, so name the way *out*, not the setting that just
        # raised.
        raise ImproperlyConfigured(
            f"{problem} To downgrade this to a warning while you migrate, set "
            "REST_FRAMEWORK_MCP['REQUIRE_TOOL_PERMISSIONS'] = False."
        )
    warnings.warn(
        f"{problem} This is a warning because "
        "REST_FRAMEWORK_MCP['REQUIRE_TOOL_PERMISSIONS'] is False; it is an "
        "error by default since 0.25.0.",
        UnguardedToolWarning,
        stacklevel=3,
    )


class UndescribedToolWarning(UserWarning):
    """A tool was registered with no description.

    Its own category, matching ``UnguardedToolWarning``.
    """


def check_tool_description_present(name: str, description: str | None, *, require: bool) -> None:
    """Warn (or raise) when a tool binding carries no description.

    Deliberately **no docstring fallback.** A docstring is written for the next
    developer, not for a model choosing between tools, so promoting one would
    ship prose never reviewed for that audience and silence the warning that
    would have prompted someone to write the right thing. The decorator paths
    that already fall back to ``fn.__doc__`` keep doing so; this reports
    whatever survived that.
    """
    if description and description.strip():
        return
    message = (
        f"MCP tool {name!r} is registered with no description. `tools/list` will "
        "advertise it with an empty description, which is the only thing a model "
        "reads to decide whether and how to call it. Pass description='...' at "
        "registration, or set REST_FRAMEWORK_MCP['REQUIRE_TOOL_DESCRIPTIONS'] = True "
        "to make this an error."
    )
    if require:
        raise ImproperlyConfigured(message)
    warnings.warn(message, UndescribedToolWarning, stacklevel=3)


class UnboundedListWarning(UserWarning):
    """A LIST selector tool was registered without pagination.

    Its own category, matching ``UnguardedToolWarning``.
    """


def check_list_pagination_declared(name: str, *, paginate: bool, require: bool) -> None:
    """Warn (or raise) when a LIST selector tool has no pagination.

    **Why this warns rather than silently clamping.** A ``paginate=True`` tool
    clamps safely because ``totalPages`` / ``hasNext`` tell the model rows were
    left behind; an unpaginated result carries no such metadata, so a clamped
    one would look complete. There is nowhere honest to put the truth except
    the registration, hence a warning here and ``MAX_RESULT_BYTES`` as the
    backstop at dispatch.
    """
    if paginate:
        return
    message = (
        f"MCP tool {name!r} is a LIST selector registered with paginate=False, so a "
        "call returns every row the selector resolves to. Unlike a paginated tool "
        "there is no honest way to clamp that at dispatch — the result carries no "
        "metadata that would tell the model rows were dropped — so an oversized "
        "result can only fail the call (see REST_FRAMEWORK_MCP['MAX_RESULT_BYTES']). "
        "Pass paginate=True, or set REST_FRAMEWORK_MCP['REQUIRE_LIST_PAGINATION'] = "
        "True to make this an error."
    )
    if require:
        raise ImproperlyConfigured(message)
    warnings.warn(message, UnboundedListWarning, stacklevel=3)


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

    Empty dict when the tool declares no link, so the caller can hand the
    result to ``merge_meta`` unconditionally.

    Three ways a link can be wrong, each of which would otherwise fail at
    runtime as a view that silently never renders, so all three raise here:
    ``ui=`` and a ``"ui"`` key in ``meta=`` together (same ``_meta`` key, so
    one would quietly win); a ``resource_uri`` naming no view on this server
    (a host resolves it against the server it read the tool from, so a view
    must be registered *before* the tool linking to it); and a tool not
    emitting ``structuredContent``, the payload a view renders from — checked
    against the effective value, so a project that turned it off globally is
    caught too.
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


def check_permissions_shape(label: str, permissions: Any) -> tuple[Any, ...]:
    """Normalise a registration's ``permissions=`` and refuse a shape that lies.

    **Security-relevant.** ``permissions="Scope"`` normalises to ``tuple("Scope")`` —
    five one-character entries. The tuple is non-empty, so
    ``check_tool_permissions_declared`` sees a guarded tool and stays quiet; at
    dispatch, ``check_permissions`` skips every entry that is not an
    [`MCPPermission`][rest_framework_mcp.auth.permissions.types.mcp_permission.MCPPermission],
    and the call is **allowed**. A bare string is the likely way in, being what the
    permission classes themselves accept, so it gets its own message.

    Only ``has_permission`` is required, deliberately: ``required_scopes`` has
    an implied ``[]`` default, so a permission implementing the gate and
    nothing else is legitimate, while an ``isinstance`` against the
    runtime-checkable Protocol would reject it for the missing member.
    """
    if isinstance(permissions, str):
        raise ImproperlyConfigured(
            f"{label}: permissions= was given the string {permissions!r}, which "
            "spreads into one entry per character — none of them a permission, "
            "leaving the binding ungated while reading as guarded. Pass "
            "permission objects: permissions=[ScopeRequired('...')]."
        )
    resolved: tuple[Any, ...] = tuple(permissions or ())
    invalid: list[Any] = [p for p in resolved if not callable(getattr(p, "has_permission", None))]
    if invalid:
        raise ImproperlyConfigured(
            f"{label}: permissions= contains {invalid!r}, which cannot answer "
            "has_permission(request, token). Every entry must be an MCPPermission "
            "— ScopeRequired / DjangoPermRequired, a DRFPermissionAdapter around a "
            "DRF class, or your own."
        )
    return resolved


def check_completions_declared(
    label: str,
    completions: dict[str, Any],
    completable: Iterable[str],
) -> None:
    """Refuse a completer keyed to an argument that doesn't exist.

    A misspelled completer never fires, and its failure is silence in a
    dropdown rather than a log line. The argument names are known at
    registration, so the typo is catchable at startup.
    """
    known: set[str] = set(completable)
    unknown: list[str] = sorted(set(completions) - known)
    if not unknown:
        return
    raise ImproperlyConfigured(
        f"{label}: completions name argument(s) {unknown!r} that this binding "
        f"does not have. Completable here: {sorted(known)!r}."
    )


__all__ = [
    "UnboundedListWarning",
    "UndescribedToolWarning",
    "UnguardedToolWarning",
    "build_ui_tool_meta",
    "check_completions_declared",
    "check_list_pagination_declared",
    "check_permissions_shape",
    "check_tool_description_present",
    "check_tool_permissions_declared",
]
