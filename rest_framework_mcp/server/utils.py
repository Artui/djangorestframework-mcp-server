"""Internal helpers shared by :class:`MCPServer`'s registration methods."""

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


class UndescribedToolWarning(UserWarning):
    """A tool was registered with no description.

    Dedicated category, matching :class:`UnguardedToolWarning`, so consumers
    can silence or escalate it precisely via ``warnings.filterwarnings``.
    """


def check_tool_description_present(name: str, description: str | None, *, require: bool) -> None:
    """Warn (or raise) when a tool binding carries no description.

    The counterpart to :func:`check_tool_permissions_declared`, and added
    for the asymmetry it closes: two properties are equally required for a
    tool to be *usable* by a model — something must gate the call, and
    something must tell the model what the call does — and only the first
    was checked. A tool registered without a description was served with
    an empty one in ``tools/list``, indistinguishable from a documented
    tool anywhere in the package, the transport, or the test surface. The
    consumer who reported this found theirs by dumping every registered
    tool and reading the output.

    Deliberately **no docstring fallback.** ``register_specs`` could
    default to ``inspect.getdoc(spec.service)``, but a docstring is
    written for the next developer, not for a model choosing between
    tools — quietly promoting one to a tool description ships prose that
    was never reviewed for that audience, and silences the warning that
    would have prompted someone to write the right thing. The decorator
    registration paths that already fall back to ``fn.__doc__`` keep doing
    so; this check simply reports whatever survived that.

    Emits on every undescribed registration (no warn-once module state —
    see the repo's no-module-level-mutable-state rule).
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

    Dedicated category, matching :class:`UnguardedToolWarning`, so consumers
    can silence or escalate it precisely via ``warnings.filterwarnings``.
    """


def check_list_pagination_declared(name: str, *, paginate: bool, require: bool) -> None:
    """Warn (or raise) when a LIST selector tool has no pagination.

    The third member of the registration-time family, and the one with a
    production incident behind it: an unpaginated LIST tool serialises whatever
    its selector returns, which for a plain ``Model.objects.all()`` is the whole
    table. The failure is not a crash — it is a response large enough to
    exhaust the client's context, or slow enough that the client gives up
    first.

    **Why this warns rather than silently clamping.** A ``paginate=True`` tool
    can have its ``limit`` clamped safely, because ``totalPages`` / ``hasNext``
    tell the model rows were left behind. An unpaginated result carries no such
    metadata, so a clamped one would look complete — and a model reasoning from
    a silently truncated list is worse off than one that got an error. There is
    nowhere honest to put the truth except the registration, hence a warning
    here and ``MAX_RESULT_BYTES`` as the backstop at dispatch.

    Emits on every unpaginated LIST registration (no warn-once module state —
    see the repo's no-module-level-mutable-state rule). ``RETRIEVE`` selectors
    are exempt: a single instance is bounded by construction.
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


def check_permissions_shape(label: str, permissions: Any) -> tuple[Any, ...]:
    """Normalise a registration's ``permissions=`` and refuse a shape that lies.

    ⚠ **Security-relevant, not tidiness.** ``permissions="Scope"`` normalises to
    ``tuple("Scope")`` — five one-character entries. The tuple is non-empty, so
    :func:`check_tool_permissions_declared` sees a guarded tool and stays quiet;
    at dispatch, :func:`check_permissions` skips every entry that is not an
    :class:`MCPPermission`, and the call is **allowed**. The end state is a
    binding that reads as guarded at the registration site, satisfies the
    unguarded-tool check, and gates nothing.

    A bare string is the likely way in — it is what the two permission classes
    themselves accept — so it gets its own message. Anything else that cannot
    answer ``has_permission`` is refused with the offending entry named.

    ⚠ Only ``has_permission`` is required, deliberately. ``MCPPermission``
    documents ``required_scopes`` as having an implied ``[]`` default, so a
    custom permission that implements the gate and nothing else is legitimate
    — an ``isinstance`` check against the runtime-checkable Protocol would
    reject it, because that checks for *every* member.

    Returns the normalised tuple, so callers use this in place of
    ``tuple(permissions or ())``.
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

    A completer for ``langauge`` is not a completer that never fires with a
    warning in the logs — it is silence in a dropdown, on a surface nobody has
    a test for. The argument names are known at registration (a prompt
    declares them, a URI template contains them), so the typo is catchable
    exactly once, at startup.

    A binding with **no** completable arguments and no completers is the
    ordinary case and passes straight through.
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
