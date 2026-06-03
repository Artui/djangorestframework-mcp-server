from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions
from rest_framework_mcp.constants import OutputFormat, UnknownArguments
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding


def chain_steps_to_tool(
    *,
    name: str,
    steps: tuple[ChainStep, ...],
    description: str | None = None,
    title: str | None = None,
    display_name: str | None = None,
    display_description: str | None = None,
    input_serializer: type | None = None,
    atomic: bool = True,
    output_alias: str | None = None,
    output_all: bool = False,
    output_format: OutputFormat = OutputFormat.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    always_listed: bool = False,
) -> ChainToolBinding:
    """Lift an ordered list of :class:`ChainStep` into a :class:`ChainToolBinding`.

    Pure projection — no side effects on the specs or their callables. The
    handler layer (``handlers/chain_tool_dispatch.py``) runs the steps.

    Each step's ``spec.permission_classes`` (sister-repo 0.12+) is wrapped
    via :func:`wrap_spec_permissions` and AND-combined with the chain-level
    ``permissions``. Because permissions are evaluated up front and all must
    pass, this is the chain's "a failing step permission blocks the whole
    chain" semantics — without running any step. Structural validation
    (non-empty, unique aliases, known ``output_alias``, spec types) happens
    in :meth:`ChainToolBinding.__post_init__`.
    """
    step_perms: tuple[Any, ...] = ()
    for step in steps:
        step_perms = step_perms + wrap_spec_permissions(
            step.spec.permission_classes, label=f"{name}:{step.alias}"
        )
    effective_perms: tuple[Any, ...] = step_perms + tuple(permissions)
    return ChainToolBinding(
        name=name,
        description=description,
        title=title,
        display_name=display_name,
        display_description=display_description,
        steps=steps,
        input_serializer=input_serializer,
        atomic=atomic,
        output_alias=output_alias,
        output_all=output_all,
        output_format=output_format,
        permissions=effective_perms,
        rate_limits=rate_limits,
        annotations=annotations or {},
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        unknown_arguments=unknown_arguments,
        always_listed=always_listed,
    )


__all__ = ["chain_steps_to_tool"]
