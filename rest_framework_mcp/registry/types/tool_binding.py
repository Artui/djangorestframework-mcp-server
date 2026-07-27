from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
# ``ExtraT`` mirrors the upstream ``ServiceSpec.ExtraT`` bound — providers
# always return a kwargs dict, never a non-mapping value.
ExtraT = TypeVar("ExtraT", bound=dict[str, Any])


@dataclass(frozen=True)
class ToolBinding(Generic[InputT, ResultT, ExtraT]):
    """All wiring for a single MCP tool, derived from a ``ServiceSpec``.

    A tool is the projection of a service callable plus its declared input
    and output serializers. The MCP server invokes ``spec.service`` directly
    via ``resolve_callable_kwargs`` + ``run_service`` — there is no view or
    viewset in the dispatch path.

    The ``Generic[InputT, ResultT, ExtraT]`` parameters mirror
    ``ServiceSpec``'s generics and are purely informational for type
    checkers. They default to ``Any`` when omitted, so existing call sites
    keep working unchanged.
    """

    name: str
    description: str | None
    spec: ServiceSpec[InputT, ResultT, ExtraT]
    display_name: str | None = None
    """Consumer-only label — **never emitted on the MCP wire** (``tools/list``
    ignores it). Provided so a downstream library can render a richer label
    than the protocol ``title``. ``None`` means "unset"."""

    display_description: str | None = None
    """Consumer-only blurb, the sibling of :attr:`display_name` — also never
    emitted on the MCP wire. Lets a downstream library show more than the
    protocol ``description``. ``None`` means "unset"."""
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    include_structured_content: bool | None = None
    """Tri-state override for whether this tool's ``tools/call`` response
    includes a ``structuredContent`` field. ``None`` (the default) defers to the
    ``INCLUDE_STRUCTURED_CONTENT`` setting; ``True`` / ``False`` force the
    behaviour regardless of the global."""

    include_output_schema: bool | None = None
    """Tri-state override for whether this tool's ``tools/list`` entry carries
    an ``outputSchema``. ``None`` (the default) defers to the
    ``INCLUDE_OUTPUT_SCHEMA`` setting; ``True`` / ``False`` force the behaviour
    regardless of the global.

    The MCP spec forbids advertising ``outputSchema`` while suppressing
    ``structuredContent``, so ``include_output_schema=True`` together with
    ``include_structured_content=False`` is rejected at construction time."""

    argument_binding: ArgumentBinding = ArgumentBinding.BUNDLE
    """How MCP ``arguments`` flow into the kwarg pool. Defaults to ``BUNDLE``
    for service tools: mutation services typically take a single
    ``input_serializer``-validated ``data`` payload, so spreading the dict as
    top-level kwargs would conflict with that shape."""

    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    """How unknown ``arguments`` keys are handled relative to the binding's
    ``inputSchema``.

    - ``REJECT`` (default) rejects unknown keys with ``-32602`` and advertises
      ``additionalProperties: false`` — but **only** when there is an
      ``input_serializer`` to validate against. A serializer-less binding has no
      declared field set, so ``REJECT`` can't fire and its schema stays open
      (``additionalProperties: true``).
    - ``PASSTHROUGH`` advertises ``additionalProperties: true`` and merges
      unknown keys into the validated payload.
    - ``IGNORE`` advertises ``additionalProperties: true`` and silently drops
      them."""

    always_listed: bool = False
    """Opt this binding back into listings it would otherwise be filtered out
    of. When ``FILTER_LISTINGS_BY_PERMISSIONS`` is enabled, a binding is
    normally dropped from ``tools/list`` if any of its ``permissions`` deny the
    caller; ``True`` keeps it visible — useful as a discovery aid for admin
    tools the caller can see but not invoke (``tools/call`` still 403s)."""

    url_kwargs: tuple[UrlKwarg, ...] = ()
    """URL-derived values the model supplies as tool args, seeded into the
    off-HTTP view's ``kwargs`` rather than reaching the service as ordinary
    params — from there drf-services spreads them into the dispatch pools, so a
    scoping ``spec.kwargs`` provider reading ``view.kwargs`` sees them. See
    :class:`UrlKwarg`. Advertised in the ``inputSchema`` and stripped from the
    dispatched params."""

    def __post_init__(self) -> None:
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Tool {self.name!r}: include_output_schema=True is incompatible "
                "with include_structured_content=False. The MCP spec requires that "
                "any tool advertising outputSchema also return conforming "
                "structuredContent. Set one of them differently."
            )

    @property
    def service(self) -> Callable[..., ResultT]:
        return self.spec.service


__all__ = ["ToolBinding"]
