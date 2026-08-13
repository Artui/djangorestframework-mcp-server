from __future__ import annotations

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


def ui_view_to_resource(
    *,
    name: str,
    uri: str,
    template_name: str | None = None,
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

    - ``template_name`` — a Django template, rendered per read with **no
      context**. This is the idiomatic choice and the deliberate one: hosts
      may prefetch and cache a view before any tool call, so a view is a
      *shell*, hydrated at runtime from tool results. Rendering tenant data
      into it would leak it across the cache.
    - ``html`` — a literal document, for a view small enough to inline.
    - ``selector`` — a zero-argument callable returning the document, for a
      project that assembles it some other way.
    """
    sources = [s for s in (template_name, html, selector) if s is not None]
    if len(sources) != 1:
        raise ValueError(
            f"register_ui_resource({name!r}) needs exactly one content source — "
            "template_name=, html= or selector=. "
            f"Got {len(sources)}."
        )

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
