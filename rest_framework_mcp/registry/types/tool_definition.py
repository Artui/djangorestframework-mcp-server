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


@dataclass(frozen=True)
class ToolDefinition:
    """Declarative description of a single tool, fed to :func:`register_tools`.

    ``ToolDefinition`` is a transport-agnostic container — it holds the
    kwargs that would otherwise be passed to
    :meth:`MCPServer.register_service_tool` or
    :meth:`MCPServer.register_selector_tool`, plus a :class:`ToolKind`
    discriminator that selects between them at dispatch time.

    Construct via the classmethods, not the dataclass constructor — the
    methods enforce the per-kind kwarg surface (a service definition
    can't set ``filter_set``; a selector definition can't omit
    ``input_serializer`` quietly etc.). Direct construction is available
    for tests and tooling but bypasses the type-shape guarantees.

    Every per-call kwarg defaults to ``None``; downstream
    :func:`register_tools` treats ``None`` as "no override", which lets
    a :class:`SelectorDefaults` / :class:`ServiceDefaults` instance
    supply the value, falling back to the registration method's own
    default if neither is set.
    """

    kind: ToolKind
    name: str
    spec: ServiceSpec | SelectorSpec
    description: str | None = None
    title: str | None = None
    # Both kinds:
    output_format: OutputFormat | None = None
    permissions: Sequence[Any] | None = None
    rate_limits: Sequence[Any] | None = None
    annotations: dict[str, Any] | None = None
    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    argument_binding: ArgumentBinding | None = None
    unknown_arguments: UnknownArguments | None = None
    # Selector-only:
    input_serializer: type | None = None
    filter_set: Any | None = None
    ordering_fields: Sequence[str] | None = None
    paginate: bool | None = None
    # Phase 10g — per-binding opt-back-in to ``tools/list`` when
    # ``FILTER_LISTINGS_BY_PERMISSIONS`` would otherwise hide this
    # binding. ``None`` means "use the registration default" (which is
    # ``False``); ``True``/``False`` force the behaviour.
    always_listed: bool | None = None
    # Explicit opt-in declaring that ``spec.kwargs(view, request)``
    # supplies these required callable parameters at dispatch time.
    # Trust must be declared per-transport because ``spec.kwargs`` is a
    # runtime callable whose output depends on the view context (URL
    # path params in DRF, URI template vars in MCP resources, neither
    # in MCP tools). ``None`` means "no kwargs provider opt-in"; supply
    # a sequence to acknowledge that the provider is the static source
    # for those names.
    spec_kwargs_provides: Sequence[str] | None = None

    @classmethod
    def service(
        cls,
        *,
        name: str,
        spec: ServiceSpec,
        description: str | None = None,
        title: str | None = None,
        output_format: OutputFormat | None = None,
        permissions: Sequence[Any] | None = None,
        rate_limits: Sequence[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding | None = None,
        unknown_arguments: UnknownArguments | None = None,
        always_listed: bool | None = None,
        spec_kwargs_provides: Sequence[str] | None = None,
    ) -> ToolDefinition:
        """Typed entry point for service-tool definitions."""
        return cls(
            kind=ToolKind.SERVICE,
            name=name,
            spec=spec,
            description=description,
            title=title,
            output_format=output_format,
            permissions=permissions,
            rate_limits=rate_limits,
            annotations=annotations,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            spec_kwargs_provides=spec_kwargs_provides,
        )

    @classmethod
    def selector(
        cls,
        *,
        name: str,
        spec: SelectorSpec,
        description: str | None = None,
        title: str | None = None,
        input_serializer: type | None = None,
        output_format: OutputFormat | None = None,
        permissions: Sequence[Any] | None = None,
        rate_limits: Sequence[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        filter_set: Any | None = None,
        ordering_fields: Sequence[str] | None = None,
        paginate: bool | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding | None = None,
        unknown_arguments: UnknownArguments | None = None,
        always_listed: bool | None = None,
        spec_kwargs_provides: Sequence[str] | None = None,
    ) -> ToolDefinition:
        """Typed entry point for selector-tool definitions.

        The selector's ``LIST`` / ``RETRIEVE`` shape lives on the spec
        (``SelectorSpec.kind``, required in
        ``djangorestframework-services`` 0.13+), not here — the bulk
        registration loop reads it from there.
        """
        return cls(
            kind=ToolKind.SELECTOR,
            name=name,
            spec=spec,
            description=description,
            title=title,
            input_serializer=input_serializer,
            output_format=output_format,
            permissions=permissions,
            rate_limits=rate_limits,
            annotations=annotations,
            filter_set=filter_set,
            ordering_fields=ordering_fields,
            paginate=paginate,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            spec_kwargs_provides=spec_kwargs_provides,
        )


__all__ = ["ToolDefinition"]
