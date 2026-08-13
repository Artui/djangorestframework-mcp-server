from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import UNSET, UnsetType
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import (
    OutputFormat,
    TaskPolicy,
    ToolContentKind,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.utils import validate_content_kind


@dataclass(frozen=True)
class ChainToolBinding:
    """All wiring for a single MCP tool that runs a **sequence of specs**.

    A chain tool threads a [`ChainContext`][rest_framework_mcp.registry.types.chain_context.ChainContext]
    through its ordered ``steps`` — each step's result is stored under its
    alias and readable by later steps — so one tool call can express
    ``retrieve x → write y → write z`` with ``z`` derived from both ``x`` and
    ``y``. Sequencing is a transport concern owned by the MCP layer; the steps
    themselves are ordinary ``ServiceSpec`` / ``SelectorSpec`` units of API
    behaviour.

    Fields not listed below mirror
    [`ToolBinding`][rest_framework_mcp.registry.types.tool_binding.ToolBinding].

    Attributes:
        steps: The ordered steps, run front to back. Non-empty, and aliases
            must be unique.
        input_serializer: The chain's input schema and validation. ``None``
            falls back to the **first step's**
            ``ServiceSpec.input_serializer``; a first selector step has none,
            so the chain then validates nothing and ``ctx.args`` is the raw
            arguments mapping.
        atomic: Run the whole sequence inside one ``transaction.atomic()``, so
            any step raising rolls back every prior write. Per-step
            ``spec.atomic`` is subordinate — steps run with ``atomic=False``
            under the chain transaction.
        output_alias: Which step's result is rendered as the tool response.
            ``None`` renders the **last** step. Mutually exclusive with
            ``output_all``.
        output_all: Render ``{alias: rendered}`` for every step that declares
            an output serializer.
    """

    name: str
    description: str | None
    steps: tuple[ChainStep, ...]
    display_name: str | None = None
    """Consumer-only label, **never emitted on the MCP wire**, so a downstream
    library can render a richer label than the protocol ``title``."""

    display_description: str | None = None
    """Consumer-only blurb, the sibling of ``display_name`` and likewise
    never emitted on the MCP wire."""
    input_serializer: type | None = None
    atomic: bool = True
    output_alias: str | None = None
    output_all: bool = False
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # Free-form for the reason given on ``ToolBinding.meta``.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this tool's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    include_structured_content: bool | None = None
    include_output_schema: bool | None = None
    # As ``ToolBinding.max_result_bytes`` / ``.dispatch_timeout``. The deadline
    # covers the whole sequence rather than each step: the client is waiting on
    # one ``tools/call``.
    max_result_bytes: int | None | UnsetType = UNSET
    dispatch_timeout: float | None | UnsetType = UNSET
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    always_listed: bool = False

    content_kind: ToolContentKind = ToolContentKind.TEXT
    """What this tool's payload becomes in the result's ``content`` array.
    ``TEXT`` renders JSON per ``output_format``; the other kinds project it
    into an image / audio / resource-link block. See [`ToolContentKind`][rest_framework_mcp.constants.ToolContentKind]."""

    content_mime_type: str | None = None
    """The media type for an ``IMAGE`` / ``AUDIO`` ``content_kind``.
    Required for those and meaningless for the rest — a resource link carries
    its own ``mimeType`` per entry."""

    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN
    """Whether calling this tool hands back a task handle instead of a result.
    The choice lives on the binding because the extension makes the *server*
    the sole decider and gives the client no way to ask. See
    [`TaskPolicy`][rest_framework_mcp.constants.TaskPolicy]."""

    invalidates: tuple[str, ...] = ()
    """URI templates naming the resources a successful call changed.

    Same contract as [`invalidates`][rest_framework_mcp.registry.types.tool_binding.ToolBinding.invalidates]:
    published as ``notifications/resources/updated`` once the transaction
    commits, rendered against the result merged with the call's arguments, and
    matched exactly — so name the collection too if you want it watched::

        invalidates=("invoices://{pk}", "invoices://")"""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ImproperlyConfigured(f"Chain tool {self.name!r}: at least one step is required.")
        aliases: list[str] = [s.alias for s in self.steps]
        dupes: set[str] = {a for a in aliases if aliases.count(a) > 1}
        if dupes:
            raise ImproperlyConfigured(
                f"Chain tool {self.name!r}: duplicate step alias(es) {sorted(dupes)!r}."
            )
        for step in self.steps:
            if not isinstance(step.spec, ServiceSpec | SelectorSpec):
                raise ImproperlyConfigured(
                    f"Chain tool {self.name!r}: step {step.alias!r} spec must be a "
                    f"ServiceSpec or SelectorSpec, got {type(step.spec).__name__}."
                )
            if isinstance(step.spec, SelectorSpec) and step.spec.selector is None:
                raise ImproperlyConfigured(
                    f"Chain tool {self.name!r}: selector step {step.alias!r} has no "
                    "selector. Set SelectorSpec(selector=...)."
                )
        if self.output_all and self.output_alias is not None:
            raise ImproperlyConfigured(
                f"Chain tool {self.name!r}: output_all=True is incompatible with "
                f"output_alias={self.output_alias!r}. Choose one."
            )
        if self.output_alias is not None and self.output_alias not in aliases:
            raise ImproperlyConfigured(
                f"Chain tool {self.name!r}: output_alias={self.output_alias!r} is not a "
                f"known step alias {sorted(set(aliases))!r}."
            )
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Chain tool {self.name!r}: include_output_schema=True is incompatible "
                "with include_structured_content=False. The MCP spec requires that any "
                "tool advertising outputSchema also return conforming structuredContent. "
                "Set one of them differently."
            )
        validate_content_kind(
            name=self.name,
            content_kind=self.content_kind,
            content_mime_type=self.content_mime_type,
            include_structured_content=self.include_structured_content,
            include_output_schema=self.include_output_schema,
        )

    @property
    def output_step(self) -> ChainStep:
        """The step whose result is rendered (``output_alias`` or the last)."""
        if self.output_alias is not None:
            return next(s for s in self.steps if s.alias == self.output_alias)
        return self.steps[-1]

    @property
    def resolved_input_serializer(self) -> type | None:
        """The serializer used to validate the chain's ``arguments``.

        ``input_serializer`` when set, else the **first step's**
        ``ServiceSpec.input_serializer``. Shared by the ``tools/list`` schema
        builder and the dispatcher, so the advertised schema and the validation
        cannot drift.
        """
        if self.input_serializer is not None:
            return self.input_serializer
        first = self.steps[0].spec
        return first.input_serializer if isinstance(first, ServiceSpec) else None

    @property
    def output_serializer(self) -> type | None:
        """The serializer the rendered output goes through, for ``outputSchema``.

        The output step's own (``ServiceSpec.output_selector_spec.
        output_serializer`` or ``SelectorSpec.output_serializer``). ``None``
        under ``output_all``, where the response is a multi-key object with no
        single schema, or when the output step declares no serializer.
        """
        if self.output_all:
            return None
        spec = self.output_step.spec
        if isinstance(spec, ServiceSpec):
            return (
                spec.output_selector_spec.output_serializer if spec.output_selector_spec else None
            )
        return spec.output_serializer


__all__ = ["ChainToolBinding"]
