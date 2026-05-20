from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments

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
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    # Tri-state override for whether this tool's ``tools/call`` response
    # includes a ``structuredContent`` field. ``None`` (the default) defers
    # to the ``INCLUDE_STRUCTURED_CONTENT`` setting; ``True`` / ``False``
    # force the behavior regardless of the global.
    include_structured_content: bool | None = None
    # Tri-state override for whether this tool's ``tools/list`` entry
    # carries an ``outputSchema``. ``None`` (the default) defers to the
    # ``INCLUDE_OUTPUT_SCHEMA`` setting; ``True`` / ``False`` force the
    # behavior regardless of the global. The MCP spec forbids advertising
    # ``outputSchema`` while suppressing ``structuredContent``, so setting
    # ``include_output_schema=True`` with ``include_structured_content=False``
    # is rejected at construction time.
    include_output_schema: bool | None = None
    # How MCP ``arguments`` flow into the kwarg pool. Defaults to
    # ``DATA_ONLY`` for service tools: mutation services typically take
    # a single ``input_serializer``-validated ``data`` payload, so
    # spreading the dict as top-level kwargs would conflict with that
    # historical shape.
    argument_binding: ArgumentBinding = ArgumentBinding.DATA_ONLY
    # How unknown ``arguments`` keys are handled relative to the binding's
    # ``inputSchema``. ``REJECT`` (default) advertises
    # ``additionalProperties: false`` and rejects unknown keys with
    # ``-32602``. ``PASSTHROUGH`` advertises ``additionalProperties: true``
    # and merges unknown keys into the validated payload. ``IGNORE``
    # advertises ``additionalProperties: true`` and silently drops them.
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    # When ``FILTER_LISTINGS_BY_PERMISSIONS`` is enabled, this binding is
    # normally dropped from ``tools/list`` if any of its ``permissions``
    # deny the caller. Setting ``always_listed=True`` opts the binding
    # back into the listing — useful as a discovery aid for admin tools
    # the caller can see but not invoke (``tools/call`` still 403s).
    always_listed: bool = False

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
