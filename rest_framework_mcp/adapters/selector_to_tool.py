from __future__ import annotations

from typing import Any

from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.adapters.utils import (
    merge_tool_annotations,
    validate_input_serializer_against_callable,
)
from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions
from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


def selector_spec_to_tool(
    *,
    name: str,
    spec: SelectorSpec,
    description: str | None = None,
    title: str | None = None,
    display_name: str | None = None,
    display_description: str | None = None,
    input_serializer: type | None = None,
    output_format: OutputFormat = OutputFormat.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    ordering_fields: tuple[str, ...] = (),
    paginate: bool = False,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    argument_binding: ArgumentBinding = ArgumentBinding.MERGE,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    always_listed: bool = False,
    spec_kwargs_provides: tuple[str, ...] = (),
) -> SelectorToolBinding:
    """Lift a ``SelectorSpec`` into a :class:`SelectorToolBinding`.

    Sister of :func:`service_spec_to_tool` for the read-shaped pipeline.
    Validation of the spec (must have a concrete ``selector`` callable)
    happens here so registration fails loudly rather than at first call.

    ``input_serializer`` is a tool-layer parameter — the spec doesn't
    carry it because the sister repo's read-views derive their args from
    the URL/query, not a request body. MCP tools always pass arguments
    as a JSON dict, so we surface the serializer on the binding instead.

    The selector's shape (``LIST`` vs ``RETRIEVE``) is read from
    ``spec.kind`` — a required field on ``SelectorSpec`` since
    ``djangorestframework-services`` 0.13. No separate ``kind`` kwarg is
    accepted here; the spec is the single source of truth.

    Filtering follows the same rule: ``spec.filter_set``
    (``djangorestframework-services`` 0.18+) is read off the spec, not
    passed here, so the filterable shape is declared once and shared by
    the HTTP and MCP transports. ``ordering_fields`` / ``paginate`` stay
    binding-level — they are MCP pipeline mechanics with no spec analogue.
    """
    if spec.selector is None:
        raise ValueError(
            f"SelectorSpec for selector tool {name!r} has selector=None — MCP needs a "
            "concrete callable to dispatch to."
        )
    validate_input_serializer_against_callable(
        label=f"selector tool {name!r}",
        input_serializer=input_serializer,
        callable_=spec.selector,
        argument_binding=argument_binding,
        spec_kwargs_provides=frozenset(spec_kwargs_provides),
    )
    spec_perms: tuple[Any, ...] = wrap_spec_permissions(spec.permission_classes, label=name)
    effective_perms: tuple[Any, ...] = spec_perms + tuple(permissions)
    return SelectorToolBinding(
        name=name,
        description=description,
        title=title,
        display_name=display_name,
        display_description=display_description,
        spec=spec,
        input_serializer=input_serializer,
        output_format=output_format,
        permissions=effective_perms,
        rate_limits=rate_limits,
        annotations=merge_tool_annotations(annotations, read_only=True),
        ordering_fields=ordering_fields,
        paginate=paginate,
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        argument_binding=argument_binding,
        unknown_arguments=unknown_arguments,
        always_listed=always_listed,
    )


__all__ = ["selector_spec_to_tool"]
