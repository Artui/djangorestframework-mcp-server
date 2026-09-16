from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services import UNSET, FieldMarking, UnsetType
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.adapters.utils import (
    merge_meta,
    merge_tool_annotations,
    validate_serializer_shapes,
)
from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions
from rest_framework_mcp.constants import (
    OutputFormat,
    TaskPolicy,
    ToolContentKind,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding


def chain_steps_to_tool(
    *,
    name: str,
    steps: tuple[ChainStep, ...],
    description: str | None = None,
    title: str | None = None,
    icons: tuple[Icon, ...] = (),
    content_kind: ToolContentKind = ToolContentKind.TEXT,
    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
    invalidates: tuple[str, ...] = (),
    content_mime_type: str | None = None,
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
    meta: dict[str, Any] | None = None,
    field_audiences: Mapping[str, FieldMarking] | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    always_listed: bool = False,
    max_result_bytes: int | None | UnsetType = UNSET,
    dispatch_timeout: float | None | UnsetType = UNSET,
) -> ChainToolBinding:
    """Lift an ordered list of
    [`ChainStep`][rest_framework_mcp.registry.types.chain_step.ChainStep] into a
    [`ChainToolBinding`][rest_framework_mcp.registry.types.chain_tool_binding.ChainToolBinding].

    Pure projection — no side effects on the specs or their callables. The
    handler layer (``handlers/chain_tool_dispatch.py``) runs the steps.

    Each step's ``spec.permission_classes`` is wrapped via
    ``wrap_spec_permissions`` and AND-combined with the chain-level
    ``permissions``. They are all evaluated up front, which is what makes a
    failing step permission block the whole chain without running any step.
    Structural validation (non-empty, unique aliases, known ``output_alias``,
    spec types) happens in ``ChainToolBinding.__post_init__``.

    ``meta`` is the base-protocol ``_meta`` bundle the tool's ``tools/list``
    entry carries — see ``service_spec_to_tool``.
    """
    step_perms: tuple[Any, ...] = ()
    for step in steps:
        step_perms = step_perms + wrap_spec_permissions(
            step.spec.permission_classes, label=f"{name}:{step.alias}"
        )
    effective_perms: tuple[Any, ...] = step_perms + tuple(permissions)
    # A chain is read-only only when every step is a selector; any service
    # step makes the whole chain a mutation (it may write).
    read_only: bool = all(isinstance(step.spec, SelectorSpec) for step in steps)
    binding = ChainToolBinding(
        name=name,
        field_audiences=field_audiences,
        description=description,
        title=title,
        icons=icons,
        content_kind=content_kind,
        task_policy=task_policy,
        invalidates=invalidates,
        content_mime_type=content_mime_type,
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
        annotations=merge_tool_annotations(annotations, read_only=read_only),
        meta=merge_meta(meta),
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        unknown_arguments=unknown_arguments,
        always_listed=always_listed,
        max_result_bytes=max_result_bytes,
        dispatch_timeout=dispatch_timeout,
    )
    # After construction rather than before, unlike the two spec adapters: the
    # serializers a chain actually uses are read off the binding, which is where
    # "explicit input, else the first step's" and "the output step, or every step
    # under output_all" are decided, and where ``__post_init__`` has already
    # refused an empty or ill-aliased chain those rules would otherwise index into.
    validate_serializer_shapes(
        label=f"chain tool {name!r}", input_serializer=binding.resolved_input_serializer
    )
    for step in binding.steps if binding.output_all else (binding.output_step,):
        validate_serializer_shapes(
            label=f"chain tool {name!r}, step {step.alias!r}",
            output_serializer=_step_output_serializer(step),
        )
    return binding


def _step_output_serializer(step: ChainStep) -> type | None:
    """The serializer a step renders through -- the same lookup the dispatcher makes."""
    spec = step.spec
    if isinstance(spec, SelectorSpec):
        return spec.output_serializer
    return spec.output_selector_spec.output_serializer if spec.output_selector_spec else None


__all__ = ["chain_steps_to_tool"]
