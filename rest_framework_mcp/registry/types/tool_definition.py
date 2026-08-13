from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import (
    ArgumentBinding,
    OutputFormat,
    ToolKind,
    UnknownArguments,
)
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg


@dataclass(frozen=True)
class ToolDefinition:
    """Declarative description of a single tool, fed to [`register_tools`][rest_framework_mcp.registry.register_tools.register_tools].

    A transport-agnostic container for the kwargs that would otherwise be
    passed to [`MCPServer.register_service_tool`][rest_framework_mcp.server.mcp_server.MCPServer.register_service_tool] or
    [`MCPServer.register_selector_tool`][rest_framework_mcp.server.mcp_server.MCPServer.register_selector_tool], plus a [`ToolKind`][rest_framework_mcp.constants.ToolKind]
    discriminator selecting between them at dispatch time.

    Construct via ``service`` / ``selector``, which enforce the
    per-kind kwarg surface; direct construction is available for tests and
    tooling but bypasses that. Filtering is declared on the spec
    (``SelectorSpec.filter_set``), so neither kind carries a ``filter_set``
    kwarg.

    Every per-call kwarg defaults to ``None``, which [`register_tools`][rest_framework_mcp.registry.register_tools.register_tools]
    reads as "no override" — letting a [`SelectorDefaults`][rest_framework_mcp.registry.types.selector_defaults.SelectorDefaults] /
    [`ServiceDefaults`][rest_framework_mcp.registry.types.service_defaults.ServiceDefaults] supply the value, and falling back to the
    registration method's own default when neither does.
    """

    kind: ToolKind
    name: str
    spec: ServiceSpec | SelectorSpec
    description: str | None = None
    title: str | None = None
    display_name: str | None = None
    """Consumer-only label, **never emitted on the MCP wire**. Carried onto the
    resulting binding so a downstream library can render a richer label than
    the protocol ``title``."""

    display_description: str | None = None
    """Consumer-only blurb, the sibling of ``display_name`` and likewise
    never emitted on the MCP wire."""

    # Both kinds:
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None
    # Selector-only:
    input_serializer: type | None = None
    ordering_fields: Sequence[str] | None = None
    paginate: bool | None = None
    always_listed: bool | None = None
    """Keep this binding in ``tools/list`` when
    ``FILTER_LISTINGS_BY_PERMISSIONS`` would otherwise hide it. ``None`` takes
    the registration default (``False``)."""

    spec_kwargs_provides: Sequence[str] | None = None
    """Explicit opt-in declaring that ``spec.kwargs(view, request)`` supplies
    these required callable parameters at dispatch time.

    Trust has to be declared **per transport**, because ``spec.kwargs`` is a
    runtime callable whose output depends on the view context — URL path params
    under DRF, URI template vars for MCP resources, neither for MCP tools.
    Supply a sequence to acknowledge that the provider is the static source for
    those names."""

    url_kwargs: Sequence[UrlKwarg] | None = None
    """URL-derived values the model supplies as tool args, seeded into the
    off-HTTP ``view.kwargs`` at dispatch. See [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg]."""

    query_params: Sequence[QueryParam] | None = None
    """Read-shaping values the model supplies as tool args, seeded into the
    off-HTTP ``request.query_params`` at dispatch. See [`QueryParam`][rest_framework_services.types.query_param.QueryParam]."""

    @classmethod
    def service(
        cls,
        *,
        name: str,
        spec: ServiceSpec,
        description: str | None = None,
        title: str | None = None,
        display_name: str | None = None,
        display_description: str | None = None,
        output_format: OutputFormat | None = None,
        permissions: Sequence[Any] | None = None,
        rate_limits: Sequence[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding | None = None,
        unknown_arguments: UnknownArguments | None = None,
        always_listed: bool | None = None,
        spec_kwargs_provides: Sequence[str] | None = None,
        url_kwargs: Sequence[UrlKwarg] | None = None,
        query_params: Sequence[QueryParam] | None = None,
    ) -> ToolDefinition:
        """Typed entry point for service-tool definitions."""
        return cls(
            kind=ToolKind.SERVICE,
            name=name,
            spec=spec,
            description=description,
            title=title,
            display_name=display_name,
            display_description=display_description,
            output_format=output_format,
            permissions=permissions,
            rate_limits=rate_limits,
            annotations=annotations,
            meta=meta,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            spec_kwargs_provides=spec_kwargs_provides,
            url_kwargs=url_kwargs,
            query_params=query_params,
        )

    @classmethod
    def selector(
        cls,
        *,
        name: str,
        spec: SelectorSpec,
        description: str | None = None,
        title: str | None = None,
        display_name: str | None = None,
        display_description: str | None = None,
        input_serializer: type | None = None,
        output_format: OutputFormat | None = None,
        permissions: Sequence[Any] | None = None,
        rate_limits: Sequence[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        ordering_fields: Sequence[str] | None = None,
        paginate: bool | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding | None = None,
        unknown_arguments: UnknownArguments | None = None,
        always_listed: bool | None = None,
        spec_kwargs_provides: Sequence[str] | None = None,
        url_kwargs: Sequence[UrlKwarg] | None = None,
        query_params: Sequence[QueryParam] | None = None,
    ) -> ToolDefinition:
        """Typed entry point for selector-tool definitions.

        The ``LIST`` / ``RETRIEVE`` shape lives on the spec
        (``SelectorSpec.kind``), not here — the bulk registration loop reads it
        from there.
        """
        return cls(
            kind=ToolKind.SELECTOR,
            name=name,
            spec=spec,
            description=description,
            title=title,
            display_name=display_name,
            display_description=display_description,
            input_serializer=input_serializer,
            output_format=output_format,
            permissions=permissions,
            rate_limits=rate_limits,
            annotations=annotations,
            meta=meta,
            ordering_fields=ordering_fields,
            paginate=paginate,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            spec_kwargs_provides=spec_kwargs_provides,
            url_kwargs=url_kwargs,
            query_params=query_params,
        )


__all__ = ["ToolDefinition"]
