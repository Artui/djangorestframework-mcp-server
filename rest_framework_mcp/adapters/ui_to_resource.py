from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from django.template.loader import render_to_string
from rest_framework_services import UNSET, UnsetType
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.adapters.utils import merge_meta
from rest_framework_mcp.constants import (
    UI_META_KEY,
    UI_RESOURCE_MIME_TYPE,
    ResourceEncoding,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.registry.types.ui_resource_meta import UIResourceMeta
from rest_framework_mcp.ui.build_app_document import build_app_document


def ui_view_to_resource(
    *,
    name: str,
    uri: str,
    template_name: str | None = None,
    body_template_name: str | None = None,
    diagnostics: bool | None = None,
    html: str | None = None,
    selector: Callable[[], str] | None = None,
    description: str | None = None,
    title: str | None = None,
    icons: tuple[Icon, ...] = (),
    cache_ttl_ms: int | UnsetType = UNSET,
    ui: UIResourceMeta | None = None,
    mime_type: str = UI_RESOURCE_MIME_TYPE,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    always_listed: bool = False,
) -> ResourceBinding:
    """Lift an interactive HTML view into a
    [`ResourceBinding`][rest_framework_mcp.registry.types.resource_binding.ResourceBinding].

    The sibling of ``selector_to_resource`` for the MCP Apps extension.
    A view is an ordinary resource with three things fixed: the Apps mime
    type, ``TEXT`` body encoding (JSON-encoding HTML would return a quoted
    string literal, not a document), and a ``_meta`` bundle under the Apps key.

    Being an ordinary resource is the point: URI-collision checking, the listing
    handlers, permission filtering and ``always_listed`` all work with no
    special case, and a view can still be guarded with ``permissions=``.

    Exactly one content source:

    - ``body_template_name`` — a Django template holding the view's *markup
      only*, wrapped here in a document that carries the ``ui/*`` bridge. The
      recommended source, and the only one that does not ask a project to
      implement the extension's postMessage protocol itself — see
      [`build_app_document`][rest_framework_mcp.ui.build_app_document.build_app_document].
      ``diagnostics=`` rides with it: whether a protocol failure is written
      into the document as well as logged, ``None`` following
      ``settings.DEBUG``.
    - ``template_name`` — a Django template holding a **whole document**.
      Everything the view needs, the bridge included, is then the template's own
      responsibility.
    - ``html`` — a literal document, for a view small enough to inline.
    - ``selector`` — a callable returning the document, for a project that
      assembles it some other way. It **must take no arguments**, and one that
      declares a parameter is refused here rather than guarded later: the read
      path resolves a selector against a pool carrying ``request`` and ``user``,
      so a parameter would be handed the authenticated caller — which is exactly
      what the permissions exemption on views assumes cannot happen.

    Both template sources render with **no context**, and that is deliberate:
    hosts may prefetch and cache a view before any tool call, so a view is a
    *shell*, hydrated at runtime from tool results. Rendering tenant data into
    one would leak it across the cache.
    """
    sources = [s for s in (template_name, body_template_name, html, selector) if s is not None]
    if len(sources) != 1:
        raise ValueError(
            f"register_ui_resource({name!r}) needs exactly one content source — "
            "body_template_name=, template_name=, html= or selector=. "
            f"Got {len(sources)}."
        )

    if selector is not None:
        _refuse_caller_aware_selector(name, selector)

    if ui is not None and meta is not None and UI_META_KEY in meta:
        raise ValueError(
            f"register_ui_resource({name!r}) got both ui= and a {UI_META_KEY!r} key in "
            "meta=. Both write the same _meta key, so one would silently win. Pass "
            "the typed ui= alone, or drop it and hand-write the whole bundle in meta=."
        )

    resolved: Callable[[], str]
    if template_name is not None:
        # Rendered per read rather than once at registration so a template edit
        # shows up without a restart, matching every other Django template.
        def resolved() -> str:
            return render_to_string(template_name)

    elif body_template_name is not None:
        # Rendered per read for the same reason a whole-document template is —
        # an edit shows up without a restart — while the shell and the bridge
        # around it are the package's, composed from a source string cached for
        # the life of the process because it ships in the wheel.
        def resolved() -> str:
            return build_app_document(
                render_to_string(body_template_name),
                title=title or name,
                diagnostics=diagnostics,
            )

    elif html is not None:

        def resolved() -> str:
            return html

    else:
        resolved = selector  # ty: ignore[invalid-assignment] - narrowed by the count check

    ui_meta: dict[str, Any] = {UI_META_KEY: ui.to_dict()} if ui is not None else {}

    return ResourceBinding(
        name=name,
        uri_template=uri,
        description=description,
        title=title,
        icons=icons,
        cache_ttl_ms=cache_ttl_ms,
        selector=resolved,
        # A view has no output serializer, so ``kind`` never reaches one; it is
        # RETRIEVE because a view is one document, not a collection.
        kind=SelectorKind.RETRIEVE,
        mime_type=mime_type,
        encoding=ResourceEncoding.TEXT,
        permissions=permissions,
        rate_limits=rate_limits,
        annotations=annotations or {},
        meta=merge_meta(ui_meta, meta),
        always_listed=always_listed,
    )


__all__ = ["ui_view_to_resource"]


def _refuse_caller_aware_selector(name: str, selector: Callable[..., str]) -> None:
    """Refuse a view selector that would be handed the caller.

    A view is the one registration on this server that skips
    ``check_tool_permissions_declared``, and the exemption rests entirely on a
    view's content being caller-blind: hosts may prefetch a view and reuse the
    document for whoever they serve next, which is why the template renders with
    no context and why an unguarded view exposes nothing.

    ``selector=`` was the hole in that argument. It is documented and typed as a
    zero-argument callable, but nothing enforced it, and ``handle_resources_read``
    resolves every binding's selector by name against a pool that deliberately
    carries ``request`` and ``user``. So ``selector=lambda user: ...`` was handed
    the authenticated caller, registered without permissions because of the
    exemption, and served into a document a host may cache across callers --
    three assumptions that are each fine alone.

    Refused here rather than guarded later, for the reason every other
    registration-time refusal on this server exists: the failure is invisible at
    runtime. A caller-aware view does not misbehave, it just quietly varies, and
    whoever reads the exemption's comment stops looking.

    ``*args`` is left alone: ``resolve_callable_kwargs`` only ever builds
    keyword arguments, so a variadic-positional selector is still called with
    nothing.
    """
    parameters = inspect.signature(selector).parameters
    fillable: list[str] = [
        parameter_name
        for parameter_name, parameter in parameters.items()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    if not fillable:
        return
    raise ValueError(
        f"register_ui_resource({name!r}) got a selector taking {fillable!r}. A view's "
        "selector must take no arguments: it is read through the same keyword pool as "
        "any other resource, which carries `request` and `user`, so a parameter here "
        "is handed the authenticated caller. Views skip the permissions-declared check "
        "precisely because they cannot read the caller, and hosts may cache one "
        "document across callers. Build the document from nothing and let the view "
        "hydrate itself from tool results, or register it with `register_resource` as "
        "a data resource, where the declaration check applies."
    )
