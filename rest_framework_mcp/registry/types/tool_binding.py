from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import UNSET, UnsetType
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments
from rest_framework_mcp.registry.types.query_param import QueryParam
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
    # Base-protocol ``_meta`` for this tool's ``tools/list`` entry, emitted
    # verbatim under the ``"_meta"`` wire key. Stays a free-form dict — and
    # not a closed dataclass — because ``_meta`` is MCP's open extension
    # namespace: each extension owns its own top-level key, so the set of
    # valid keys is unbounded by design. Typed helpers belong *above* this
    # field (a caller builds a typed object and merges its ``to_dict()`` in
    # via :func:`~rest_framework_mcp.adapters.utils.merge_meta`), not in
    # place of it.
    meta: dict[str, Any] = field(default_factory=dict)
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

    max_result_bytes: int | None | UnsetType = UNSET
    """Per-tool override for the outbound result ceiling. ``UNSET`` (the
    default) defers to the server's ``MAX_RESULT_BYTES``; ``None`` disables the
    check for this tool; an ``int`` sets its own ceiling.

    ``None`` cannot double as "not supplied" here — disabling the ceiling for
    one deliberately-large export tool is a real thing to want — which is why
    this is ``UNSET``-sentinelled rather than tri-state like the fields above."""

    dispatch_timeout: float | None | UnsetType = UNSET
    """Per-tool override for the dispatch deadline, in seconds. ``UNSET``
    defers to the server's ``DISPATCH_TIMEOUT``; ``None`` disables it for this
    tool; a number sets its own. ⚠ Async transport only — see
    :attr:`~rest_framework_mcp.config.types.mcp_config.MCPConfig.dispatch_timeout`."""

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

    query_params: tuple[QueryParam, ...] = ()
    """Read-shaping params routed to ``request.query_params`` at dispatch.

    Popped from the caller's arguments like a URL kwarg, but landing in the
    synthetic request's ``GET`` rather than ``view.kwargs`` — the channel a
    serializer reads when it branches on the query string. A ``filter_set``
    field is **not** one of these; see ``split_query_params``."""

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
