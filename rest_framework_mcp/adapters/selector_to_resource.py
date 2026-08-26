from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_services import UNSET, UnsetType
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.adapters.utils import merge_meta
from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions
from rest_framework_mcp.constants import ResourceEncoding
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

# Every ``SelectorSpec`` field this adapter cannot carry, paired with the value
# that means "not set". ``resources/read`` dispatches the bare callable through
# ``run_selector``, so none of these reach the read: a ``preconditions`` gate
# would not run, a ``filter_set`` would not filter, an
# ``output_serializer_context`` provider would not be resolved. Registration
# refuses rather than dropping them, because a gate that silently does not run
# on one transport while holding on every other is the worst of the three
# outcomes and is indistinguishable from success.
_UNCARRIED_SPEC_FIELDS: tuple[tuple[str, Any], ...] = (
    ("allow_none", False),
    ("annotations", None),
    ("extend_queryset", None),
    ("filter_set", None),
    ("metadata", None),
    ("output_serializer_context", None),
    ("prefetch_related", None),
    ("preconditions", None),
    ("progress_reporter", None),
    ("select_related", None),
)


def selector_to_resource(
    *,
    name: str,
    uri_template: str,
    selector: SelectorSpec,
    description: str | None = None,
    title: str | None = None,
    icons: tuple[Icon, ...] = (),
    cache_ttl_ms: int | UnsetType = UNSET,
    completions: dict[str, Callable[..., Any]] | None = None,
    output_serializer: type | None = None,
    mime_type: str = "application/json",
    encoding: ResourceEncoding = ResourceEncoding.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    always_listed: bool = False,
) -> ResourceBinding:
    """Lift a ``SelectorSpec`` into a
    [`ResourceBinding`][rest_framework_mcp.registry.types.resource_binding.ResourceBinding].

    Mirrors ``service_spec_to_tool`` — the unit of registration is always a
    spec from ``djangorestframework-services``. ``.selector`` must be set (there
    is nothing to dispatch to otherwise), ``.output_serializer`` fills in when
    the caller passed none, and ``.kwargs`` becomes the binding's per-request
    kwargs provider. Dispatch happens at ``resources/read`` time through
    ``run_selector`` / ``arun_selector``, so async selectors work transparently.

    ``meta`` is the base-protocol ``_meta`` bundle the resource's listing
    entry and its ``resources/read`` contents block carry — see
    ``service_spec_to_tool``.

    Only ``.selector``, ``.kind``, ``.output_serializer``, ``.kwargs`` and
    ``.permission_classes`` are carried. A spec setting any *other* behavioural
    field is refused here, naming the fields: the resource read path dispatches
    the bare callable, so those fields would be dropped, and a dropped
    ``preconditions`` gate is a hole rather than an inconvenience. Register such
    a spec as a selector *tool*, which does honour them, or move the behaviour
    into the callable.
    """
    if not isinstance(selector, SelectorSpec):
        raise TypeError(
            f"register_resource(selector=...) requires a SelectorSpec; got "
            f"{type(selector).__name__}. Wrap your callable in "
            f"`SelectorSpec(selector=fn)` (or use the @server.resource "
            f"decorator, which wraps the function automatically)."
        )
    if selector.selector is None:
        raise ValueError(
            f"SelectorSpec for resource {name!r} has selector=None — MCP needs a "
            "concrete callable to dispatch to."
        )
    # Identity, not equality: the sentinels are ``None`` and ``False``, both
    # singletons, and a spec field may hold a class or a callable whose
    # ``__eq__`` is not this function's business. ``getattr``'s fallback keeps
    # the check working against a sister release that has not grown one of them.
    uncarried: list[str] = [
        field
        for field, unset in _UNCARRIED_SPEC_FIELDS
        if getattr(selector, field, unset) is not unset
    ]
    if uncarried:
        listed: str = ", ".join(repr(field) for field in uncarried)
        raise ValueError(
            f"SelectorSpec for resource {name!r} sets {listed}, which the resource "
            "read path does not apply — it dispatches the selector callable "
            "directly, so these would be silently dropped (a 'preconditions' gate "
            "would simply not run). Register this spec as a selector tool, which "
            "honours them, or fold the behaviour into the callable."
        )
    # Spec values fill in caller-omitted kwargs only; an explicit caller
    # argument is an intentional choice and is never overridden.
    resolved_callable: Callable[..., Any] = selector.selector
    if output_serializer is None:
        output_serializer = selector.output_serializer
    kwargs_provider = selector.kwargs

    spec_perms: tuple[Any, ...] = wrap_spec_permissions(selector.permission_classes, label=name)
    effective_perms: tuple[Any, ...] = spec_perms + tuple(permissions)
    return ResourceBinding(
        name=name,
        uri_template=uri_template,
        description=description,
        title=title,
        icons=icons,
        cache_ttl_ms=cache_ttl_ms,
        completions=dict(completions or {}),
        selector=resolved_callable,
        kind=selector.kind,
        output_serializer=output_serializer,
        mime_type=mime_type,
        encoding=encoding,
        permissions=effective_perms,
        rate_limits=rate_limits,
        annotations=annotations or {},
        meta=merge_meta(meta),
        kwargs_provider=kwargs_provider,
        always_listed=always_listed,
    )


__all__ = ["selector_to_resource"]
