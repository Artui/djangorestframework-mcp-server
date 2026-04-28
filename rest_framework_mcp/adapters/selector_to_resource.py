from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.registry.resource_binding import ResourceBinding


def selector_to_resource(
    *,
    name: str,
    uri_template: str,
    selector: SelectorSpec,
    description: str | None = None,
    title: str | None = None,
    output_serializer: type | None = None,
    mime_type: str = "application/json",
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
) -> ResourceBinding:
    """Lift a :class:`SelectorSpec` into a :class:`ResourceBinding`.

    Mirrors :func:`service_spec_to_tool` — the unit of registration is always
    a spec from ``djangorestframework-services``. ``.selector`` must be set
    (a spec with ``selector=None`` is rejected because there's nothing to
    dispatch to), ``.output_serializer`` fills in when the caller didn't pass
    one explicitly, and ``.kwargs`` becomes the binding's per-request kwargs
    provider.

    The selector is dispatched at ``resources/read`` time via
    ``run_selector`` / ``arun_selector`` so async selectors work
    transparently.
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
    # Spec values fill in caller-omitted kwargs. We don't override
    # explicit caller args because those represent intentional choices.
    resolved_callable: Callable[..., Any] = selector.selector
    if output_serializer is None:
        output_serializer = selector.output_serializer
    kwargs_provider = selector.kwargs

    return ResourceBinding(
        name=name,
        uri_template=uri_template,
        description=description,
        title=title,
        selector=resolved_callable,
        output_serializer=output_serializer,
        mime_type=mime_type,
        permissions=permissions,
        rate_limits=rate_limits,
        annotations=annotations or {},
        kwargs_provider=kwargs_provider,
    )


__all__ = ["selector_to_resource"]
