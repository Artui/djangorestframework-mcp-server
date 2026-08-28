from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Generic, TypeVar

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import (
    UNSET,
    AudienceProjection,
    FieldMarking,
    UnsetType,
    build_audience_projection,
)
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import (
    ArgumentBinding,
    OutputFormat,
    TaskPolicy,
    ToolContentKind,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.registry.types.utils import validate_content_kind

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
# Mirrors the upstream ``ServiceSpec.ExtraT`` bound: providers always return a
# kwargs dict, never a non-mapping value.
ExtraT = TypeVar("ExtraT", bound=dict[str, Any])


@dataclass(frozen=True)
class ToolBinding(Generic[InputT, ResultT, ExtraT]):
    """All wiring for a single MCP tool, derived from a ``ServiceSpec``.

    A tool is the projection of a service callable plus its declared input and
    output serializers. The MCP server invokes ``spec.service`` directly via
    ``resolve_callable_kwargs`` + ``run_service`` — there is no view or viewset
    in the dispatch path.

    ``annotations`` and ``meta`` are emitted verbatim on this tool's
    ``tools/list`` entry, under ``annotations`` and ``_meta`` respectively.

    The generic parameters mirror ``ServiceSpec``'s and are purely
    informational for type checkers, defaulting to ``Any`` when omitted.
    """

    name: str
    description: str | None
    spec: ServiceSpec[InputT, ResultT, ExtraT]
    display_name: str | None = None
    """Consumer-only label, **never emitted on the MCP wire**, so a downstream
    library can render a richer label than the protocol ``title``."""

    display_description: str | None = None
    """Consumer-only blurb, the sibling of ``display_name`` and likewise
    never emitted on the MCP wire."""
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # A free-form dict rather than a dataclass because ``_meta`` is MCP's open
    # extension namespace: each extension owns a top-level key, so the valid
    # key set is unbounded by design. Typed helpers belong *above* this field —
    # a caller builds a typed object and merges its ``to_dict()`` in via
    # ``merge_meta`` — not in place of it.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this tool's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    include_structured_content: bool | None = None
    """Whether this tool's ``tools/call`` response carries
    ``structuredContent``. ``None`` defers to the
    ``INCLUDE_STRUCTURED_CONTENT`` setting."""

    include_output_schema: bool | None = None
    """Whether this tool's ``tools/list`` entry carries an ``outputSchema``.
    ``None`` defers to the ``INCLUDE_OUTPUT_SCHEMA`` setting.

    The MCP spec forbids advertising ``outputSchema`` while suppressing
    ``structuredContent``, so ``True`` together with
    ``include_structured_content=False`` is rejected at construction."""

    max_result_bytes: int | None | UnsetType = UNSET
    """Per-tool outbound result ceiling. ``UNSET`` defers to the server's
    ``MAX_RESULT_BYTES``, ``None`` disables the check for this tool, an ``int``
    sets its own. Sentinelled rather than tri-state because ``None`` here means
    "no ceiling", which a deliberately-large export tool genuinely wants."""

    dispatch_timeout: float | None | UnsetType = UNSET
    """Per-tool dispatch deadline, in seconds. ``UNSET`` defers to the server's
    ``DISPATCH_TIMEOUT``, ``None`` disables it here. Async transport only — see
    ``dispatch_timeout``."""

    argument_binding: ArgumentBinding = ArgumentBinding.BUNDLE
    """How MCP ``arguments`` flow into the kwarg pool. ``BUNDLE`` for service
    tools, because a mutation service typically takes one
    ``input_serializer``-validated ``data`` payload and spreading the dict as
    top-level kwargs would conflict with that shape."""

    unknown_arguments: UnknownArguments = UnknownArguments.REJECT
    """How unknown ``arguments`` keys are handled relative to the binding's
    ``inputSchema``.

    - ``REJECT`` (default) answers ``-32602`` and advertises
      ``additionalProperties: false`` — but **only** with an
      ``input_serializer`` to validate against. A serializer-less binding has
      no declared field set, so ``REJECT`` cannot fire and its schema stays
      open.
    - ``PASSTHROUGH`` advertises an open schema and merges unknown keys into
      the validated payload.
    - ``IGNORE`` advertises an open schema and drops them."""

    always_listed: bool = False
    """Keep this binding in ``tools/list`` even when
    ``FILTER_LISTINGS_BY_PERMISSIONS`` would drop it because its
    ``permissions`` deny the caller. A discovery aid for tools the caller can
    see but not invoke — ``tools/call`` still 403s."""

    query_params: tuple[QueryParam, ...] = ()
    """Read-shaping params routed to ``request.query_params`` at dispatch.

    Popped from the caller's arguments like a URL kwarg, but landing in the
    synthetic request's ``GET`` rather than ``view.kwargs`` — the channel a
    serializer reads when it branches on the query string. A ``filter_set``
    field is **not** one of these."""

    url_kwargs: tuple[UrlKwarg, ...] = ()
    """URL-derived values the model supplies as tool args, seeded into the
    off-HTTP view's ``kwargs`` instead of reaching the service as ordinary
    params, so a scoping ``spec.kwargs`` provider reading ``view.kwargs`` sees
    them. Advertised in the ``inputSchema`` and stripped from the dispatched
    params. See [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg]."""

    content_kind: ToolContentKind = ToolContentKind.TEXT
    """What this tool's payload becomes in the result's ``content`` array. ``TEXT``
    renders JSON per ``output_format``; the other kinds project it into an image / audio
    / resource-link block. See
    [`ToolContentKind`][rest_framework_mcp.constants.ToolContentKind]."""

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

    Published as ``notifications/resources/updated`` once the transaction
    commits, so subscribers re-read. Same ``{var}`` syntax as a resource's
    ``uri_template``, rendered against the result merged with the call's
    arguments:

        invalidates=("invoices://{pk}", "invoices://")

    **Name the collection too if you want it watched.** Topic matching is
    exact — a prefix rule would match ``invoices://1`` against
    ``invoices://11`` and miss a tenant-scoped scheme entirely.

    Only calls that go through this server fire it. A management command, a
    Celery job or an admin edit changes the same rows and publishes nothing;
    ``MCPServer.notify_resource_updated`` covers those."""

    field_audiences: Mapping[str, FieldMarking] | None = None
    """Per-tool overrides layered over the ``FieldMarking`` declarations the
    output serializer carries on its own fields.

    The serializer stays authoritative — it is the one declaration the REST API,
    this transport, and an in-process toolset all read. This exists for the case
    one tool genuinely needs what a sibling hides: a lookup tool returning the
    identifier its neighbour drops.

    Declared on the registry entry's
    [`OfflineContract`][rest_framework_services.types.offline_contract.OfflineContract]
    and resolved here, so the field set an agent sees does not depend on which
    agent transport served it."""

    @property
    def agent_output_serializer(self) -> type | None:
        """The serializer whose rendered output reaches the caller, if any."""
        spec = self.spec
        return spec.output_selector_spec.output_serializer if spec.output_selector_spec else None

    @cached_property
    def audience_projection(self) -> AudienceProjection:
        """This tool's resolved audience markings, derived once per binding.

        Drives both the projected payload and the advertised ``outputSchema``,
        so the two cannot disagree about which fields a caller will receive."""
        return build_audience_projection(
            self.agent_output_serializer,
            overrides=self.field_audiences,
            name=f"Tool {self.name!r}",
        )

    def __post_init__(self) -> None:
        if self.include_output_schema is True and self.include_structured_content is False:
            raise ImproperlyConfigured(
                f"Tool {self.name!r}: include_output_schema=True is incompatible "
                "with include_structured_content=False. The MCP spec requires that "
                "any tool advertising outputSchema also return conforming "
                "structuredContent. Set one of them differently."
            )
        validate_content_kind(
            name=self.name,
            content_kind=self.content_kind,
            content_mime_type=self.content_mime_type,
            include_structured_content=self.include_structured_content,
            include_output_schema=self.include_output_schema,
        )

    @property
    def service(self) -> Callable[..., ResultT]:
        return self.spec.service


__all__ = ["ToolBinding"]
