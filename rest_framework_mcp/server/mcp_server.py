from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.urls import URLPattern, path
from rest_framework.serializers import Serializer
from rest_framework_services import UNSET, OfflineContract, UnsetType
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.validate_channel_names import validate_channel_names

from rest_framework_mcp.adapters.chain_to_tool import chain_steps_to_tool
from rest_framework_mcp.adapters.selector_to_resource import selector_to_resource
from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool
from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool
from rest_framework_mcp.adapters.ui_to_resource import ui_view_to_resource
from rest_framework_mcp.adapters.utils import merge_meta
from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
)
from rest_framework_mcp.auth.protected_resource_metadata import ProtectedResourceMetadataViewSet
from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.check_removed_settings import check_removed_settings
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import (
    JSONRPC_VERSION,
    RESOURCE_UPDATED_METHOD,
    UI_RESOURCE_MIME_TYPE,
    ArgumentBinding,
    NotificationKind,
    OutputFormat,
    ResourceEncoding,
    TaskPolicy,
    ToolContentKind,
    UnknownArguments,
)
from rest_framework_mcp.handlers.call_spec_tool import call_spec_tool
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.protocol.build_server_info import build_server_info
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument
from rest_framework_mcp.protocol.types.tool_result import ToolResult
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.prompt_binding import PromptBinding
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.ui_resource_meta import UIResourceMeta
from rest_framework_mcp.registry.types.ui_tool_meta import UIToolMeta
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.server.utils import (
    build_ui_tool_meta,
    check_completions_declared,
    check_list_pagination_declared,
    check_permissions_shape,
    check_tool_description_present,
    check_tool_permissions_declared,
)
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.utils import (
    topic_for_kind,
    topic_for_resource,
)
from rest_framework_mcp.tasks.build_worker_token import build_worker_token
from rest_framework_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from rest_framework_mcp.tasks.run_task import run_task as _run_task
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore
from rest_framework_mcp.transport.async_streamable_http_viewset import (
    ASYNC_STREAMABLE_HTTP_ACTION_MAP,
    AsyncStreamableHttpViewSet,
)
from rest_framework_mcp.transport.django_cache_session_store import DjangoCacheSessionStore
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.streamable_http_viewset import (
    STREAMABLE_HTTP_ACTION_MAP,
    StreamableHttpViewSet,
)
from rest_framework_mcp.transport.types.session_store import SessionStore
from rest_framework_mcp.transport.types.sse_broker import SSEBroker
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer


class MCPServer:
    """A pluggable MCP server backed by ``ServiceSpec`` registrations.

    The server owns its tool and resource registries, an auth backend, and a
    session store — all instance state, no module-level singletons. Two
    parallel registration shapes are supported:

    Imperative:

        server = MCPServer(name="my-app")
        server.register_service_tool(
            name="invoices.create",
            spec=ServiceSpec(service=create_invoice, input_serializer=InvoiceInput),
        )
        server.register_resource(
            name="invoice",
            uri_template="invoices://{pk}",
            selector=SelectorSpec(selector=get_invoice, output_serializer=InvoiceOutput),
        )

    Declarative:

        @server.service_tool(name="invoices.create", input_serializer=InvoiceInput)
        def create_invoice(*, data): ...

        @server.resource(uri_template="invoices://{pk}", output_serializer=InvoiceOutput)
        def get_invoice(*, pk): ...

    Mount the URLs in your URL conf the ``admin.site.urls`` way — ``.urls`` is a
    namespaced ``(patterns, app_name, namespace)`` triple ``path()`` mounts
    directly (no ``include()``):

        urlpatterns = [path("mcp/", server.urls)]
        # reverse("mcp:endpoint") · reverse("mcp:protected-resource-metadata")
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        version: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        website_url: str | None = None,
        description: str | None = None,
        resource_url: str | None = None,
        config: MCPConfig | None = None,
        auth_backend: MCPAuthBackend | None = None,
        session_store: SessionStore | None = None,
        sse_broker: SSEBroker | None = None,
        sse_replay_buffer: SSEReplayBuffer | None = None,
        task_store: TaskStore | None | UnsetType = UNSET,
        task_executor: TaskExecutor | None = None,
        subscription_broker: SubscriptionBroker | None = None,
        url_namespace: str = "mcp",
    ) -> None:
        check_removed_settings()
        # Identity is resolved once, here, so the instance is the single source
        # of truth on the wire. Every unset kwarg defers to ``SERVER_INFO`` —
        # including ``icons=()``, which is "unset" and not a way to suppress
        # configured icons.
        self._server_info: Implementation = build_server_info(
            name=name,
            version=version,
            title=title,
            website_url=website_url,
            icons=icons or None,
        )
        self.name: str = self._server_info.name
        self.version: str = self._server_info.version
        self.title: str | None = self._server_info.title
        self.description: str | None = description
        # The scalar settings, snapshotted once and threaded to the transport
        # and every handler, so nothing reads settings on the request path and
        # two servers here can genuinely differ.
        self._config: MCPConfig = config if config is not None else build_mcp_config()
        self._url_namespace: str = url_namespace
        self._tools: ToolRegistry = ToolRegistry()
        self._resources: ResourceRegistry = ResourceRegistry()
        self._prompts: PromptRegistry = PromptRegistry()
        # ``resource_url`` configures the *default* backend; a custom backend
        # owns its own audience policy, so there is nowhere to forward it to.
        # Refusing beats leaving audience enforcement quietly unconfigured.
        if resource_url is not None and auth_backend is not None:
            raise ImproperlyConfigured(
                "Pass resource_url= or auth_backend=, not both — a custom auth "
                "backend owns its own audience binding. Configure it there, e.g. "
                f"auth_backend=DjangoOAuthToolkitBackend(resource_url={resource_url!r})."
            )
        self._auth_backend: MCPAuthBackend = (
            auth_backend
            if auth_backend is not None
            else DjangoOAuthToolkitBackend(resource_url=resource_url)
        )
        # Namespaced by default: the cache-backed store shares one Django cache
        # across every server in the process, so without this a session minted
        # at ``/public/mcp`` satisfies ``/internal/mcp``'s ownership check and a
        # DELETE against either destroys the other's session. Keyed on ``name``,
        # not ``url_namespace``: a server used only in-process is never mounted,
        # so its namespace is a meaningless default that would collide it with a
        # mounted one — and Django's duplicate-namespace check (urls.W005)
        # cannot see an unmounted server.
        self._session_store: SessionStore = (
            session_store
            if session_store is not None
            else DjangoCacheSessionStore(namespace=self.name)
        )
        # Single-process SSE works out of the box; multi-worker ASGI passes a
        # ``RedisSSEBroker`` (or any other ``SSEBroker``) explicitly.
        self._sse_broker: SSEBroker = sse_broker or InMemorySSEBroker()
        # Opt-in: ``None`` means no resume support — no ``id:`` lines,
        # ``Last-Event-ID`` silently ignored.
        self._sse_replay_buffer: SSEReplayBuffer | None = sse_replay_buffer
        # No default broker is constructed. A server that quietly got the
        # in-memory one would advertise subscription support and then deliver
        # nothing as soon as a second worker existed, and the failure looks
        # exactly like "nothing ever changed".
        self._subscription_broker: SubscriptionBroker | None = subscription_broker
        # The executor is the switch: supply one and a cache-backed store
        # appears (namespaced like the session store, for the same reason),
        # supply neither and this server runs no tasks. A store with nowhere to
        # run the work would advertise the extension, hand out handles and
        # finish none of them. ``UNSET`` keeps ``task_store=None`` distinct from
        # "not passed" — that is "I will wire the store later", not a request
        # for the default.
        self._task_executor: TaskExecutor | None = task_executor
        self._task_store: TaskStore | None
        if not isinstance(task_store, UnsetType):
            self._task_store = task_store
        elif task_executor is not None:
            self._task_store = DjangoCacheTaskStore(namespace=self.name)
        else:
            self._task_store = None

    # ----- imperative registration -----

    def register_service_tool(
        self,
        *,
        name: str,
        spec: ServiceSpec,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        display_name: str | None = None,
        display_description: str | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        invalidates: tuple[str, ...] | list[str] = (),
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        content_mime_type: str | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        agent_contract: OfflineContract | None = None,
        ui: UIToolMeta | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding = ArgumentBinding.BUNDLE,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        always_listed: bool = False,
        spec_kwargs_provides: tuple[str, ...] = (),
        url_kwargs: tuple[UrlKwarg, ...] = (),
        query_params: tuple[QueryParam, ...] = (),
        max_result_bytes: int | None | UnsetType = UNSET,
        dispatch_timeout: float | None | UnsetType = UNSET,
    ) -> ToolBinding:
        """Register a ``ServiceSpec`` as an MCP **mutation** tool.

        The dispatch pipeline runs ``input_serializer → run_service(atomic) →
        output_selector? → output_serializer``, so this is the surface for
        side-effecting operations. For read-shaped ones (list/retrieve with
        optional filtering / ordering / pagination) use
        ``register_selector_tool`` instead.

        ``meta`` is the base protocol's generic ``_meta`` bundle, emitted
        verbatim under the ``"_meta"`` key of this tool's ``tools/list`` entry
        and omitted when empty. It is *not* the ``annotations`` hint bundle —
        those are a closed, spec-defined set of client hints, while ``_meta``
        is where protocol extensions put their own keys. Passed through as
        given: no key is validated or reserved here.

        ``ui`` links this tool to an interactive view registered with
        ``register_ui_resource``, so a host renders the result inline
        instead of raw JSON. The view must already be registered on this
        server, and the tool must emit ``structuredContent`` — what the view
        renders from — or the link is refused at registration rather than
        shipping a view that comes up blank.

        ``agent_contract`` carries what a caller with **no HTTP request** has to
        be told -- the URL kwargs, query params and field-audience overrides the
        URLconf and query string give an HTTP caller for free. ``register_specs``
        passes each entry's own, so the declaration is made once and every agent
        transport reads it; an explicit ``url_kwargs`` / ``query_params`` here
        wins over it.
        """
        ui_meta = build_ui_tool_meta(
            name=name,
            ui=ui,
            meta=meta,
            resources=self._resources,
            include_structured_content=include_structured_content,
            default_structured_content=self._config.include_structured_content,
        )
        contract = agent_contract or OfflineContract()
        binding = service_spec_to_tool(
            name=name,
            field_audiences=contract.field_audiences,
            spec=spec,
            description=description,
            title=title,
            icons=icons,
            display_name=display_name,
            display_description=display_description,
            output_format=OutputFormat.coerce(output_format),
            content_kind=content_kind,
            task_policy=task_policy,
            invalidates=tuple(invalidates),
            content_mime_type=content_mime_type,
            permissions=check_permissions_shape(f"MCP binding {name!r}", permissions),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=merge_meta(ui_meta, meta),
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            spec_kwargs_provides=spec_kwargs_provides,
            # The mount's own declaration wins wherever it makes one; the
            # contract is what the registry entry says when it does not. An
            # empty tuple is how a caller says nothing here, not how it says
            # "none" -- to drop an entry's channels, override the contract.
            url_kwargs=tuple(url_kwargs) or contract.url_kwargs,
            query_params=tuple(query_params) or contract.query_params,
            max_result_bytes=max_result_bytes,
            dispatch_timeout=dispatch_timeout,
        )
        check_tool_permissions_declared(
            binding.name, binding.permissions, require=self._config.require_tool_permissions
        )
        check_tool_description_present(
            binding.name, binding.description, require=self._config.require_tool_descriptions
        )
        # Per binding, and eagerly, so a spec-violating pair fails at import
        # time rather than on the first ``tools/call``. A server-wide "schema
        # on, content off" is legal when every binding overrides it back on,
        # so checking the globals alone would be wrong.
        resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
            default_output_schema=self._config.include_output_schema,
            default_structured_content=self._config.include_structured_content,
        )
        self._tools.register(binding)
        return binding

    def register_selector_tool(
        self,
        *,
        name: str,
        spec: SelectorSpec,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        display_name: str | None = None,
        display_description: str | None = None,
        input_serializer: type | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        content_mime_type: str | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        agent_contract: OfflineContract | None = None,
        ui: UIToolMeta | None = None,
        ordering_fields: list[str] | tuple[str, ...] | None = None,
        paginate: bool = False,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding = ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        always_listed: bool = False,
        spec_kwargs_provides: tuple[str, ...] = (),
        url_kwargs: tuple[UrlKwarg, ...] = (),
        query_params: tuple[QueryParam, ...] = (),
        max_result_bytes: int | None | UnsetType = UNSET,
        dispatch_timeout: float | None | UnsetType = UNSET,
        max_page_size: int | None | UnsetType = UNSET,
    ) -> SelectorToolBinding:
        """Register a ``SelectorSpec`` as an MCP **read** tool.

        Read-shaped sibling of ``register_service_tool``. The selector
        returns a raw, unscoped queryset; the tool layer owns the post-fetch
        pipeline:

        ```text
        arguments → validate(merged inputSchema)
                  → run_selector
                  → FilterSet(data=...).qs    (if spec.filter_set set)
                  → order_by(...)             (if ordering_fields set)
                  → paginate                  (if paginate=True)
                  → output_serializer(many=True)
                  → ToolResult
        ```

        Each knob is optional; with none of them set the tool is a plain RPC
        read against the selector.

        Filtering is declared on the spec, not here: set
        ``SelectorSpec.filter_set`` and both the HTTP and MCP transports honour
        it. It requires the ``[filter]`` extra (``django-filter``), and schema
        generation raises a clear ``ImportError`` without it. ``ordering_fields``
        / ``paginate`` stay here, being MCP pipeline mechanics with no spec
        analogue.

        The shape comes from ``spec.kind``: ``LIST`` runs the full post-fetch
        pipeline and renders with ``many=True``; ``RETRIEVE`` rejects those
        knobs at registration and renders with ``many=False``.

        ``meta`` is the generic ``_meta`` bundle for this tool's
        ``tools/list`` entry, and ``ui`` links it to an interactive view —
        both as on ``register_service_tool``.

        ``agent_contract`` carries what a caller with **no HTTP request** has to
        be told -- the URL kwargs, query params and field-audience overrides the
        URLconf and query string give an HTTP caller for free. ``register_specs``
        passes each entry's own, so the declaration is made once and every agent
        transport reads it; an explicit ``url_kwargs`` / ``query_params`` here
        wins over it.
        """
        ui_meta = build_ui_tool_meta(
            name=name,
            ui=ui,
            meta=meta,
            resources=self._resources,
            include_structured_content=include_structured_content,
            default_structured_content=self._config.include_structured_content,
        )
        contract = agent_contract or OfflineContract()
        binding = selector_spec_to_tool(
            name=name,
            field_audiences=contract.field_audiences,
            spec=spec,
            description=description,
            title=title,
            icons=icons,
            display_name=display_name,
            display_description=display_description,
            input_serializer=input_serializer,
            output_format=OutputFormat.coerce(output_format),
            content_kind=content_kind,
            task_policy=task_policy,
            content_mime_type=content_mime_type,
            permissions=check_permissions_shape(f"MCP binding {name!r}", permissions),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=merge_meta(ui_meta, meta),
            ordering_fields=tuple(ordering_fields or ()),
            paginate=paginate,
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            spec_kwargs_provides=spec_kwargs_provides,
            # The mount's own declaration wins wherever it makes one; the
            # contract is what the registry entry says when it does not. An
            # empty tuple is how a caller says nothing here, not how it says
            # "none" -- to drop an entry's channels, override the contract.
            url_kwargs=tuple(url_kwargs) or contract.url_kwargs,
            query_params=tuple(query_params) or contract.query_params,
            max_result_bytes=max_result_bytes,
            dispatch_timeout=dispatch_timeout,
            max_page_size=max_page_size,
        )
        check_tool_permissions_declared(
            binding.name, binding.permissions, require=self._config.require_tool_permissions
        )
        check_tool_description_present(
            binding.name, binding.description, require=self._config.require_tool_descriptions
        )
        # Per binding, and eagerly, so a spec-violating pair fails at import
        # time rather than on the first ``tools/call``. A server-wide "schema
        # on, content off" is legal when every binding overrides it back on,
        # so checking the globals alone would be wrong.
        resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
            default_output_schema=self._config.include_output_schema,
            default_structured_content=self._config.include_structured_content,
        )
        # LIST only: a RETRIEVE selector returns one instance, which is bounded
        # by construction and has no ``limit`` to advertise.
        if binding.kind is SelectorKind.LIST:
            check_list_pagination_declared(
                binding.name,
                paginate=binding.paginate,
                require=self._config.require_list_pagination,
            )
        self._tools.register(binding)
        return binding

    def register_specs(
        self,
        registry: SpecRegistry,
        *,
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> tuple[ToolBinding | SelectorToolBinding, ...]:
        """Register every spec in a ``SpecRegistry`` as a tool, in order.

        A project exposing the same operations over more than one transport
        keeps its spec set in a
        ``SpecRegistry``
        so each transport reads one source. This walks it and calls
        ``register_service_tool`` / ``register_selector_tool`` per
        entry, discriminating on the spec type.

        It is a **source for** this server's own ``ToolRegistry``, not a
        replacement — every tool lands as a normal binding sharing the one
        tool namespace (a collision raises, as always). The registry carries
        only what is invariant across transports; every MCP knob stays here,
        per tool, via ``overrides``:

            server.register_specs(
                registry.by_tag("public"),
                overrides={
                    "list_orders": {"paginate": True, "ordering_fields": ["created_at"]},
                    "refund_order": {"annotations": {"destructiveHint": True}},
                },
            )


        Each entry's
        [`OfflineContract`][rest_framework_services.types.offline_contract.OfflineContract]
        comes across as the mount's default — the ``url_kwargs``,
        ``query_params`` and ``field_audiences`` an off-HTTP caller needs and an
        HTTP one gets from the URLconf and query string for free. A per-tool
        ``url_kwargs`` / ``query_params`` override wins over it; overriding
        ``agent_contract`` itself replaces it outright, which is the only way to
        register an entry with *fewer* channels than it declares.

        Keys are checked against the target method's own signature, so a knob
        used on the wrong spec kind (``paginate`` on a ``ServiceSpec``) raises
        ``TypeError`` from there. An ``overrides`` key naming a spec the
        registry doesn't hold raises ``ValueError`` here — that is a typo,
        not an intentional no-op.

        Registration is not transactional: a failure partway leaves the earlier
        entries registered, which is harmless at configuration time because a
        raise aborts startup anyway.

        Returns the bindings in registration order.
        """
        override_map = dict(overrides or {})
        unknown = sorted(name for name in override_map if name not in registry)
        if unknown:
            raise ValueError(
                f"overrides name specs not in this SpecRegistry: {unknown}. "
                f"Registered names: {sorted(entry.name for entry in registry.all())}."
            )

        bindings: list[ToolBinding | SelectorToolBinding] = []
        for entry in registry.all():
            knobs = dict(override_map.get(entry.name, {}))
            # An entry's own agent contract, unless this mount replaced it.
            # ``setdefault`` rather than a merge on purpose: the contract is one
            # object, and a mount substituting a different one has said so.
            knobs.setdefault("agent_contract", entry.agent_contract)
            if isinstance(entry.spec, ServiceSpec):
                bindings.append(
                    self.register_service_tool(name=entry.name, spec=entry.spec, **knobs)
                )
            else:
                bindings.append(
                    self.register_selector_tool(name=entry.name, spec=entry.spec, **knobs)
                )
        return tuple(bindings)

    def register_chain_tool(
        self,
        *,
        name: str,
        steps: list[ChainStep] | tuple[ChainStep, ...],
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        display_name: str | None = None,
        display_description: str | None = None,
        input_serializer: type | None = None,
        atomic: bool = True,
        output_alias: str | None = None,
        output_all: bool = False,
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        invalidates: tuple[str, ...] | list[str] = (),
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        content_mime_type: str | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        agent_contract: OfflineContract | None = None,
        ui: UIToolMeta | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        always_listed: bool = False,
        max_result_bytes: int | None | UnsetType = UNSET,
        dispatch_timeout: float | None | UnsetType = UNSET,
    ) -> ChainToolBinding:
        """Register an ordered sequence of specs as a single MCP tool.

        Each [`ChainStep`][rest_framework_mcp.registry.types.chain_step.ChainStep]
        wraps a ``ServiceSpec`` (write) or ``SelectorSpec`` (read) and binds
        its result to an alias. A step's ``inputs`` callable reads the
        validated tool arguments (``ctx.args``) and any prior step's output
        (``ctx[alias]``) to build that step's call kwargs, so one tool call can
        express ``retrieve x → write y → write z``.

        ``atomic=True`` runs the whole sequence inside one
        ``transaction.atomic()``: any step raising a ``ServiceError`` /
        ``ServiceValidationError`` rolls back every prior write and the
        JSON-RPC error carries ``failedStep``.

        The advertised ``inputSchema`` is ``input_serializer`` when set,
        otherwise the first step's serializer. The response is the
        ``output_alias`` step's rendered output (default: the last step), or
        ``{alias: rendered}`` for every serializer-bearing step when
        ``output_all=True``.

        Each step's ``spec.permission_classes`` are AND-combined with the
        chain-level ``permissions`` and evaluated up front — a failing step
        permission blocks the whole chain before any step runs.

        Chains deliberately do not run the selector post-fetch pipeline
        (filter / order / paginate); for that, expose the selector as its
        own ``register_selector_tool``.

        ``meta`` is the generic ``_meta`` bundle for this tool's
        ``tools/list`` entry, and ``ui`` links it to an interactive view —
        both as on ``register_service_tool``.

        ``agent_contract`` is the same carrier the two spec registrars take, and
        a chain's only route to it: a chain has no registry entry to inherit
        from. Only its ``field_audiences`` apply -- a chain declares its
        arguments through its steps.
        """
        ui_meta = build_ui_tool_meta(
            name=name,
            ui=ui,
            meta=meta,
            resources=self._resources,
            include_structured_content=include_structured_content,
            default_structured_content=self._config.include_structured_content,
        )
        binding = chain_steps_to_tool(
            name=name,
            field_audiences=(agent_contract or OfflineContract()).field_audiences,
            steps=tuple(steps),
            description=description,
            title=title,
            icons=icons,
            display_name=display_name,
            display_description=display_description,
            input_serializer=input_serializer,
            atomic=atomic,
            output_alias=output_alias,
            output_all=output_all,
            output_format=OutputFormat.coerce(output_format),
            content_kind=content_kind,
            task_policy=task_policy,
            invalidates=tuple(invalidates),
            content_mime_type=content_mime_type,
            permissions=check_permissions_shape(f"MCP binding {name!r}", permissions),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=merge_meta(ui_meta, meta),
            include_structured_content=include_structured_content,
            include_output_schema=include_output_schema,
            unknown_arguments=unknown_arguments,
            always_listed=always_listed,
            max_result_bytes=max_result_bytes,
            dispatch_timeout=dispatch_timeout,
        )
        check_tool_permissions_declared(
            binding.name, binding.permissions, require=self._config.require_tool_permissions
        )
        check_tool_description_present(
            binding.name, binding.description, require=self._config.require_tool_descriptions
        )
        # Per binding, and eagerly, so a spec-violating pair fails at import
        # time rather than on the first ``tools/call``. A server-wide "schema
        # on, content off" is legal when every binding overrides it back on,
        # so checking the globals alone would be wrong.
        resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
            default_output_schema=self._config.include_output_schema,
            default_structured_content=self._config.include_structured_content,
        )
        self._tools.register(binding)
        return binding

    # ----- transport-neutral invocation -----

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user: Any,
        request: Any = None,
    ) -> ToolResult:
        """Invoke a registered spec-backed tool off the HTTP / JSON-RPC path.

        The transport-neutral entry point: hand a tool ``name``, a flat ``arguments``
        dict (the role ``request.data`` / query params play on HTTP) and the acting
        ``user``, and get back the same
        [`ToolResult`][rest_framework_mcp.protocol.types.tool_result.ToolResult] the
        wire handlers build. An in-process consumer calls this instead of
        re-implementing dispatch.

        This is the **spec core only** — instance resolution, input validation,
        the service / selector run, the output-selector re-fetch, queryset
        shaping including ``filter_set``, and the retrieve nullability
        contract, shared with every other transport rather than reproduced. It
        honours the binding's ``argument_binding`` / ``unknown_arguments``
        policies and the spec's ``permission_classes`` (object-level checks
        included), but not the
        read-shaped transport extras — pagination, ordering, a selector
        binding's MCP-only ``input_serializer`` — nor the transport-level MCP
        permissions and rate limits. For those, and for tool listing, use
        ``acall_tool`` / ``list_tools``. Chain tools orchestrate
        several specs and raise ``TypeError`` here.

        Raises ``KeyError`` when no tool is registered under ``name``.
        """
        binding = self._tools.get(name)
        if binding is None:
            raise KeyError(f"No tool registered under {name!r}.")
        return call_spec_tool(
            binding, arguments or {}, user=user, request=request, config=self._config
        )

    # ----- in-process transport invocation -----

    def list_tools(
        self,
        cursor: str | None = None,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Any] | JsonRpcError:
        """List the tools this server exposes, exactly as the wire would.

        The in-process twin of a ``tools/list`` request: one page of the tool catalog
        with the *same* merged ``inputSchema`` the HTTP transport advertises (serializer
        fields plus a selector tool's filter / ordering / pagination arguments and the
        ``additionalProperties`` policy), the same per-caller listing-permission filter
        (``FILTER_LISTINGS_BY_PERMISSIONS``) and the same opaque-cursor pagination —
        pass the returned ``nextCursor`` back for the next page. A
        [`JsonRpcError`][rest_framework_mcp.protocol.types.json_rpc_error.JsonRpcError]
        signals a bad cursor.

        ``scopes`` are the caller's granted scopes; pass them so a
        ``ScopeRequired``-gated tool is visible under
        ``FILTER_LISTINGS_BY_PERMISSIONS`` exactly as it would be on the wire.

        Unlike ``call_tool`` (the spec core) this is the full transport
        surface. Under an event loop use ``alist_tools`` — a listing
        permission filter that hits the DB raises ``SynchronousOnlyOperation``
        from a sync call on the loop.
        """
        params = {"cursor": cursor} if cursor is not None else None
        return handle_tools_list(
            params, self._call_context(user=user, request=request, scopes=scopes)
        )

    async def alist_tools(
        self,
        cursor: str | None = None,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Any] | JsonRpcError:
        """Async ``list_tools`` — safe to call from an event loop.

        Listing itself is pure Python, but the per-caller permission filter
        (``FILTER_LISTINGS_BY_PERMISSIONS``) may run a DB-backed check, which
        raises ``SynchronousOnlyOperation`` when reached synchronously from
        within an event loop. The whole sync handler therefore runs in Django's
        thread-sensitive executor.
        """
        params = {"cursor": cursor} if cursor is not None else None
        context = self._call_context(user=user, request=request, scopes=scopes)
        return await sync_to_async(handle_tools_list, thread_sensitive=True)(params, context)

    async def acall_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> dict[str, Any] | JsonRpcError:
        """Invoke a tool off the HTTP path with full transport semantics (async).

        The in-process twin of a ``tools/call`` request: routes through the same async
        handler the wire uses, so the transport-level MCP permissions and rate limits,
        the selector post-fetch pipeline (filter / order / paginate), a selector
        binding's MCP-only ``input_serializer``, chain tools and the output format all
        apply — everything ``call_tool`` omits. Returns the wire's result payload (a
        ``dict`` carrying ``content`` / ``structuredContent`` / ``isError``), or a
        [`JsonRpcError`][rest_framework_mcp.protocol.types.json_rpc_error.JsonRpcError]
        for a protocol fault (unknown tool, malformed ``arguments`` shape, denied
        permission).

        ``request`` is the originating Django request when there is one; a
        minimal one is synthesised otherwise, mirroring ``call_tool``.
        ``scopes`` populate the synthetic token so a ``ScopeRequired``-gated
        tool is invokable in-process just as it is on the wire.
        """
        params = {"name": name, "arguments": arguments or {}}
        return await handle_tools_call_async(
            params, self._call_context(user=user, request=request, scopes=scopes)
        )

    def _call_context(
        self,
        *,
        user: Any,
        request: Any = None,
        scopes: Sequence[str] | None = None,
        session_id: str | None = None,
    ) -> MCPCallContext:
        """Build the per-call context the wire handlers thread through.

        When ``request`` is ``None`` a minimal
        ``HttpRequest`` is synthesised bearing the user, so
        permission classes reading ``request.user`` behave as they would on
        HTTP. The protocol version is the server's first (most preferred)
        supported version, not a hardcoded literal.
        """
        http_request: HttpRequest = request if request is not None else HttpRequest()
        if request is None:
            http_request.user = user
            http_request.method = "POST"
        return MCPCallContext(
            http_request=http_request,
            token=TokenInfo(user=user, scopes=tuple(scopes or ())),
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            protocol_version=self._config.protocol_versions[0],
            session_id=session_id,
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
            tasks=self._task_store,
            task_executor=self._task_executor,
            subscriptions=self._subscription_broker,
        )

    def run_task(self, task_id: str) -> None:
        """Execute a queued task. **This is what a worker calls.**

        The other end of ``task_executor.enqueue``, and the whole public
        surface of the worker side:

            @shared_task
            def run_mcp_task(task_id: str) -> None:
                my_server.run_task(task_id)

        Everything it needs comes out of the store: the tool, the arguments,
        and the authorization context to re-check them under. Nothing is
        returned — the client learns the outcome by polling ``tasks/get``.

        Safe to call for an id that is unknown, already claimed or already
        finished: each is a no-op. Queues deliver at least once, and a retried
        delivery must not run a mutation twice.

        Raises when the server has no task store: a worker calling this on a
        server that cannot run tasks would otherwise fail as silence — the job
        "succeeds" and the client polls a handle forever.
        """
        store: TaskStore | None = self._task_store
        if store is None:
            raise ImproperlyConfigured(
                f"MCPServer {self.name!r} has no task store, so run_task() has nothing "
                "to read. Pass task_executor= (which builds a default store) or an "
                "explicit task_store= when constructing the server."
            )
        _run_task(store, task_id, context_factory=self._worker_context)

    def _worker_context(self, record: TaskRecord) -> MCPCallContext:
        """The context a task runs under, off the request path.

        Rebuilt rather than remembered: the identity half comes back out of the
        record (see ``build_worker_token``), the registries, config and stores
        from this server.

        ``client_capabilities`` is deliberately empty. The worker is not
        serving a client, and a task must never create another task — an empty
        declaration makes ``maybe_create_task`` refuse, so a ``REQUIRED``
        binding reached this way fails visibly instead of queueing itself
        forever.
        """
        http_request = HttpRequest()
        http_request.method = "POST"
        token: TokenInfo = build_worker_token(record)
        http_request.user = token.user
        return MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            protocol_version=self._config.protocol_versions[0],
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
            tasks=self._task_store,
            task_executor=self._task_executor,
            subscriptions=self._subscription_broker,
            enforce_rate_limits=False,
        )

    async def notify_resource_updated(self, uri: str) -> int:
        """Tell every subscriber watching ``uri`` that it changed.

        The explicit trigger, and the one that always works. Call it from
        wherever the write actually happens — a management command, a Celery
        job, a ``save()`` override, a signal handler you wrote yourself:

            await server.notify_resource_updated(f"invoices://{invoice.pk}")

        Returns how many subscribers were reached — a diagnostic, not a
        guarantee: ``0`` means nobody was listening, which is the ordinary case
        and not an error. Notifications are best-effort by design; a client
        that misses one re-reads the resource.

        **A URI, not a template.** Publish the concrete URI that changed, and
        the collection URI too if watchers of the collection should hear about
        it — matching is exact, deliberately (see
        [`topic_for_resource`][rest_framework_mcp.subscriptions.utils.topic_for_resource]).

        **Publish after the transaction commits.** Inside
        ``transaction.atomic()`` this announces a change that may still roll
        back, and a subscriber that re-reads immediately sees the old value —
        worse than no notification at all. ``transaction.on_commit`` is the
        Django-native answer.
        """
        return await self._publish(
            topic_for_resource(uri),
            {
                "jsonrpc": JSONRPC_VERSION,
                "method": RESOURCE_UPDATED_METHOD,
                "params": {"uri": uri},
            },
        )

    async def notify_list_changed(self, kind: NotificationKind) -> int:
        """Tell subscribers that one of the catalogs changed.

        Rarely needed: registration happens once at configuration time, so a
        catalog is fixed for the life of the process. It exists for the server
        that registers tools from data — a plugin loader, a per-tenant
        catalog — where the list genuinely can change under a running client.
        """
        return await self._publish(
            topic_for_kind(kind), {"jsonrpc": JSONRPC_VERSION, "method": kind.method}
        )

    async def _publish(self, topic: str, payload: dict[str, Any]) -> int:
        """Hand a notification to the broker, or drop it if there is none.

        A server with no broker simply does not push, so this is a no-op rather
        than an error — which is what keeps ``notify_resource_updated`` safe to
        call unconditionally from a service.
        """
        if self._subscription_broker is None:
            return 0
        return await self._subscription_broker.publish(topic, payload)

    @property
    def subscription_broker(self) -> SubscriptionBroker | None:
        return self._subscription_broker

    @property
    def task_store(self) -> TaskStore | None:
        return self._task_store

    @property
    def task_executor(self) -> TaskExecutor | None:
        return self._task_executor

    def register_resource(
        self,
        *,
        name: str,
        uri_template: str,
        selector: SelectorSpec,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        output_serializer: type | None = None,
        mime_type: str = "application/json",
        encoding: ResourceEncoding = ResourceEncoding.JSON,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
        cache_ttl_ms: int | UnsetType = UNSET,
        completions: dict[str, Callable[..., Any]] | None = None,
    ) -> ResourceBinding:
        """Register a ``SelectorSpec`` as an MCP resource.

        ``selector.selector`` is the callable dispatched at ``resources/read``
        time; ``selector.output_serializer`` fills in when the explicit
        ``output_serializer=`` kwarg is absent (that kwarg wins);
        ``selector.kwargs`` becomes the binding's per-request kwargs provider.

        A bare callable is not accepted here — wrap it in
        ``SelectorSpec(selector=fn)``, or use the decorator form
        ``resource``, which wraps it automatically.

        The shape comes from ``selector.kind`` and drives the ``many=`` flag on
        ``output_serializer`` at dispatch; ``RETRIEVE`` is the typical case for
        a URI-template lookup.

        ``meta`` is the generic ``_meta`` bundle (see
        ``register_service_tool``) for this resource's listing entry —
        ``resources/list`` for a concrete URI, ``resources/templates/list``
        for a template — and for the ``contents`` block ``resources/read``
        returns.

        ``encoding`` decides how the selector's value becomes the read body:
        ``JSON`` pretty-prints it, ``TEXT`` returns it verbatim. Anything whose
        ``mime_type`` is not JSON — Markdown, CSV, plain text — wants ``TEXT``,
        or the document comes back wrapped in a quoted string literal. For an
        HTML view use ``register_ui_resource``, which sets both.

        A ``uri_template`` variable is a caller-controlled name that reaches the
        selector's kwarg pool, so one named after a dispatcher seed (``user``,
        ``request``, ``data`` …) or declared twice raises here rather than
        letting a URI segment stand in for the authenticated identity.

        A resource with no permissions at all is refused for the same reason a
        tool is — see ``REQUIRE_TOOL_PERMISSIONS``. The same selector exposed
        as a resource is as reachable as it is exposed as a tool.
        """
        binding = selector_to_resource(
            name=name,
            uri_template=uri_template,
            selector=selector,
            description=description,
            title=title,
            icons=icons,
            output_serializer=output_serializer,
            mime_type=mime_type,
            encoding=encoding,
            permissions=check_permissions_shape(f"MCP binding {name!r}", permissions),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=meta,
            always_listed=always_listed,
            cache_ttl_ms=cache_ttl_ms,
            completions=completions,
        )
        template_variables: tuple[str, ...] = _template_variables(uri_template)
        # A URI-template variable is a caller-controlled name routed into the
        # selector's kwarg pool, exactly like a tool's ``UrlKwarg`` — so it goes
        # through the same shared check, which owns the reserved-seed set and
        # catches a name declared twice. The transport's own post-fetch names
        # (``page`` / ``limit`` / ``ordering``) are deliberately *not* reserved
        # here: a resource has no post-fetch pipeline, so ``docs://{page}`` is a
        # legitimate locator.
        validate_channel_names(
            label=f"Resource {name!r}",
            kind="uri_template variable",
            declarations=tuple(UrlKwarg(name=variable) for variable in template_variables),
        )
        # Template variables are the only completable arguments a resource has.
        check_completions_declared(f"Resource {name!r}", binding.completions, template_variables)
        check_tool_permissions_declared(
            binding.name,
            binding.permissions,
            require=self._config.require_tool_permissions,
            kind="resource",
        )
        self._resources.register(binding)
        return binding

    def register_ui_resource(
        self,
        *,
        name: str,
        uri: str,
        template_name: str | None = None,
        html: str | None = None,
        selector: Callable[[], str] | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        ui: UIResourceMeta | None = None,
        mime_type: str = UI_RESOURCE_MIME_TYPE,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
        cache_ttl_ms: int | UnsetType = UNSET,
    ) -> ResourceBinding:
        """Register an interactive HTML view (an MCP App) as a resource.

        A tool links to the view and a **host** renders it inline in the chat,
        inside a sandboxed iframe it constructs itself. This server only
        *declares*: it serves the document and describes what it needs in
        ``_meta``. The iframe, the CSP enforcement and the ``ui/*`` postMessage
        bridge belong to the host and are deliberately not implemented here.

        Give exactly one content source — ``template_name`` (a Django template,
        the idiomatic choice), ``html`` (a literal document), or ``selector``
        (a zero-argument callable returning one).

        **Keep tenant data out of the view.** Hosts may prefetch and cache a
        view before any tool call, so it is a shell that hydrates itself at
        runtime from tool results — which is also why the template renders with
        no context.

        ``ui=`` is the typed
        [`UIResourceMeta`][rest_framework_mcp.registry.types.ui_resource_meta.UIResourceMeta]
        — CSP origins, browser permissions, publisher ``domain``, border preference —
        which serialises into ``_meta`` under the extension's key. ``meta=`` remains
        available for *other* extensions; passing both ``ui=`` and that same key inside
        ``meta=`` raises, rather than letting one silently win.

        The result is an ordinary
        [`ResourceBinding`][rest_framework_mcp.registry.types.resource_binding.ResourceBinding],
        so it shares one URI namespace with data resources (a collision raises as
        always), appears in ``resources/list``, and honours ``permissions`` /
        ``always_listed``. Views default to **unguarded** — the MCP session is already
        authenticated and a view is a static asset, not tenant data."""
        binding = ui_view_to_resource(
            name=name,
            uri=uri,
            template_name=template_name,
            html=html,
            selector=selector,
            description=description,
            title=title,
            icons=icons,
            ui=ui,
            mime_type=mime_type,
            permissions=check_permissions_shape(f"MCP binding {name!r}", permissions),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            meta=meta,
            always_listed=always_listed,
            cache_ttl_ms=cache_ttl_ms,
        )
        # No ``check_tool_permissions_declared`` here, unlike every other
        # registration on this server. A view's content sources are a template
        # rendered with no context, a literal document, or a zero-argument
        # callable — none of which can read the caller's data, so an unguarded
        # view exposes nothing an authenticated session may not already see.
        # Requiring permissions on it would be noise on every MCP Apps install.
        self._resources.register(binding)
        return binding

    def register_prompt(
        self,
        *,
        name: str,
        render: Callable[..., Any],
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        arguments: list[PromptArgument] | None = None,
        completions: dict[str, Callable[..., Any]] | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
    ) -> PromptBinding:
        """Register a render callable as an MCP prompt.

        ``render`` receives the prompt arguments as kwargs (plus ``request`` and
        ``user`` if it declares them) and returns either a string, a list of strings, a
        list of
        [`PromptMessage`][rest_framework_mcp.protocol.types.prompt_message.PromptMessage],
        or a coroutine yielding any of those — the dispatch layer normalises the result.

        ``request`` and ``user`` are seeded **over** the client's arguments at
        ``prompts/get``, so an argument named after one of them never reaches
        ``render`` in the seed's place.

        A prompt with no permissions at all is refused like a tool — see
        ``REQUIRE_TOOL_PERMISSIONS``. A ``render`` callable reads whatever its
        author gave it access to, so nothing about a prompt makes it safe by
        construction.

        ``meta`` is the generic ``_meta`` bundle for this prompt's
        ``prompts/list`` entry — see ``register_service_tool``.
        """
        binding = PromptBinding(
            name=name,
            description=description,
            title=title,
            icons=icons,
            render=render,
            arguments=tuple(arguments or ()),
            permissions=check_permissions_shape(f"MCP binding {name!r}", permissions),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations or {},
            meta=merge_meta(meta),
            always_listed=always_listed,
            completions=dict(completions or {}),
        )
        check_completions_declared(
            f"Prompt {name!r}", binding.completions, (arg.name for arg in binding.arguments)
        )
        check_tool_permissions_declared(
            binding.name,
            binding.permissions,
            require=self._config.require_tool_permissions,
            kind="prompt",
        )
        self._prompts.register(binding)
        return binding

    # ----- declarative (decorator) registration -----

    def service_tool(
        self,
        *,
        name: str,
        spec: ServiceSpec | None = None,
        input_serializer: type | None = None,
        output_serializer: type[Serializer] | None = None,
        output_selector: Callable[..., Any] | None = None,
        atomic: bool = True,
        success_status: int | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        invalidates: tuple[str, ...] | list[str] = (),
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        content_mime_type: str | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        agent_contract: OfflineContract | None = None,
        ui: UIToolMeta | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding = ArgumentBinding.BUNDLE,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        always_listed: bool = False,
        spec_kwargs_provides: tuple[str, ...] = (),
        url_kwargs: tuple[UrlKwarg, ...] = (),
        query_params: tuple[QueryParam, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of ``register_service_tool``.

        If ``spec`` is supplied it is used verbatim; otherwise a
        ``ServiceSpec`` is constructed from the keyword arguments. The
        original function is returned unchanged, so it stays callable from
        Python without going through the MCP transport.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            # Built only when there is something to put in it, so a decorator
            # with no output-side declarations carries no empty envelope.
            output_selector_spec: SelectorSpec | None = None
            if output_serializer is not None or output_selector is not None:
                output_selector_spec = SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=output_selector,
                    output_serializer=output_serializer,
                )
            effective_spec: ServiceSpec = spec or ServiceSpec(
                service=fn,
                input_serializer=input_serializer,
                output_selector_spec=output_selector_spec,
                atomic=atomic,
                success_status=success_status,
            )
            self.register_service_tool(
                name=name,
                spec=effective_spec,
                description=description or fn.__doc__,
                title=title,
                icons=icons,
                output_format=output_format,
                content_kind=content_kind,
                task_policy=task_policy,
                invalidates=tuple(invalidates),
                content_mime_type=content_mime_type,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
                meta=meta,
                agent_contract=agent_contract,
                ui=ui,
                include_structured_content=include_structured_content,
                include_output_schema=include_output_schema,
                argument_binding=argument_binding,
                unknown_arguments=unknown_arguments,
                always_listed=always_listed,
                spec_kwargs_provides=spec_kwargs_provides,
                url_kwargs=url_kwargs,
                query_params=query_params,
            )
            return fn

        return wrap

    def selector_tool(
        self,
        *,
        name: str,
        kind: SelectorKind | None = None,
        spec: SelectorSpec | None = None,
        input_serializer: type | None = None,
        output_serializer: type[Serializer] | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        output_format: OutputFormat | str = OutputFormat.JSON,
        content_kind: ToolContentKind = ToolContentKind.TEXT,
        task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
        content_mime_type: str | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        agent_contract: OfflineContract | None = None,
        ui: UIToolMeta | None = None,
        ordering_fields: list[str] | tuple[str, ...] | None = None,
        paginate: bool = False,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        argument_binding: ArgumentBinding = ArgumentBinding.SPREAD_AUTHOR_WINS,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        always_listed: bool = False,
        spec_kwargs_provides: tuple[str, ...] = (),
        url_kwargs: tuple[UrlKwarg, ...] = (),
        query_params: tuple[QueryParam, ...] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of ``register_selector_tool``.

        If ``spec`` is supplied it is used verbatim; otherwise a
        ``SelectorSpec`` is constructed from the wrapped function and the
        keyword arguments. The original function is returned unchanged, so it
        stays callable from Python without going through the MCP transport.

        ``kind`` is required when ``spec`` is omitted; otherwise it comes from
        ``spec.kind`` and any value passed here is ignored.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            if spec is None:
                if kind is None:
                    raise TypeError(
                        f"@selector_tool {name!r}: ``kind`` is required when "
                        "``spec`` is omitted — the decorator auto-constructs a "
                        "SelectorSpec and the spec's own ``kind`` field is "
                        "mandatory. Pass kind=SelectorKind.LIST | RETRIEVE."
                    )
                effective_spec: SelectorSpec = SelectorSpec(
                    kind=kind,
                    selector=fn,
                    output_serializer=output_serializer,
                )
            else:
                effective_spec = spec
            self.register_selector_tool(
                name=name,
                spec=effective_spec,
                description=description or fn.__doc__,
                title=title,
                icons=icons,
                input_serializer=input_serializer,
                output_format=output_format,
                content_kind=content_kind,
                task_policy=task_policy,
                content_mime_type=content_mime_type,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
                meta=meta,
                agent_contract=agent_contract,
                ui=ui,
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
            return fn

        return wrap

    def resource(
        self,
        *,
        uri_template: str,
        kind: SelectorKind | None = None,
        name: str | None = None,
        spec: SelectorSpec | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        output_serializer: type[Serializer] | None = None,
        mime_type: str = "application/json",
        encoding: ResourceEncoding = ResourceEncoding.JSON,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
        cache_ttl_ms: int | UnsetType = UNSET,
        completions: dict[str, Callable[..., Any]] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: register the wrapped callable as a resource.

        If ``spec`` is supplied it is used verbatim; otherwise a
        ``SelectorSpec`` is constructed from the wrapped function and the
        keyword arguments. The original function is returned unchanged, so it
        stays callable from Python without going through the MCP transport.

        ``kind`` is required when ``spec`` is omitted; otherwise it comes from
        ``spec.kind`` and any value passed here is ignored.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            if spec is None:
                if kind is None:
                    raise TypeError(
                        f"@resource {(name or getattr(fn, '__name__', 'resource'))!r}: "
                        "``kind`` is required when ``spec`` is omitted — the "
                        "decorator auto-constructs a SelectorSpec and the spec's "
                        "own ``kind`` field is mandatory. Pass "
                        "kind=SelectorKind.RETRIEVE (typical for URI templates) or "
                        "kind=SelectorKind.LIST."
                    )
                effective_spec: SelectorSpec = SelectorSpec(
                    kind=kind, selector=fn, output_serializer=output_serializer
                )
            else:
                effective_spec = spec
            self.register_resource(
                name=name or getattr(fn, "__name__", "resource"),
                uri_template=uri_template,
                selector=effective_spec,
                description=description or fn.__doc__,
                title=title,
                icons=icons,
                output_serializer=output_serializer,
                mime_type=mime_type,
                encoding=encoding,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
                meta=meta,
                always_listed=always_listed,
                cache_ttl_ms=cache_ttl_ms,
                completions=completions,
            )
            return fn

        return wrap

    def prompt(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        icons: tuple[Icon, ...] = (),
        arguments: list[PromptArgument] | None = None,
        completions: dict[str, Callable[..., Any]] | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        always_listed: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: register the wrapped callable as a prompt."""

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register_prompt(
                name=name or getattr(fn, "__name__", "prompt"),
                render=fn,
                description=description or fn.__doc__,
                title=title,
                icons=icons,
                arguments=arguments,
                completions=completions,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
                meta=meta,
                always_listed=always_listed,
            )
            return fn

        return wrap

    # ----- accessors -----

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @property
    def resources(self) -> ResourceRegistry:
        return self._resources

    @property
    def prompts(self) -> PromptRegistry:
        return self._prompts

    @property
    def config(self) -> MCPConfig:
        """This server's resolved scalars — a frozen snapshot taken at construction."""
        return self._config

    @property
    def auth_backend(self) -> MCPAuthBackend:
        return self._auth_backend

    @property
    def session_store(self) -> SessionStore:
        return self._session_store

    @property
    def sse_broker(self) -> SSEBroker:
        return self._sse_broker

    @property
    def sse_replay_buffer(self) -> SSEReplayBuffer | None:
        return self._sse_replay_buffer

    # ----- server-initiated push -----

    async def notify(self, session_id: str, payload: Any) -> bool:
        """Push a JSON-RPC payload to a session's open SSE stream.

        Returns ``True`` if a subscriber was present, ``False`` if no client is
        connected — a missed push is not generally an error, since clients pull
        state via ``tools/call`` round-trips. The broker enforces one
        subscriber per session: re-subscribing replaces the old queue silently.

        With a
        [`SSEReplayBuffer`][rest_framework_mcp.transport.types.sse_replay_buffer.SSEReplayBuffer]
        configured the payload is recorded *before* publishing, so the frame carries an
        event ID the SSE generator emits on the wire (``id: <id>\\ndata:
        <payload>\\n\\n``) and a later reconnect with ``Last-Event-ID`` drains what it
        missed before resuming live mode. Without a buffer there are no ``id:`` lines
        and resume is disabled.

        Multi-process deployments need an out-of-process broker to fan out
        across workers; the in-process broker only sees its own.
        """
        if self._sse_replay_buffer is None:
            return await self._sse_broker.publish(session_id, payload)
        event_id: str = await self._sse_replay_buffer.record(session_id, payload)
        # Wrapped so the generator can emit ``id:`` alongside ``data:``;
        # unwrapped in ``stream_events``. Brokers never see the wrapper shape.
        return await self._sse_broker.publish(
            session_id,
            {"_mcp_event_id": event_id, "_mcp_payload": payload},
        )

    # ----- URLs -----

    @property
    def urls(self) -> tuple[list[URLPattern], str, str]:
        """Sync URL patterns. Suitable for any deployment (WSGI or ASGI).

        Returns the namespaced ``(patterns, app_name, namespace)`` triple
        ``path()`` mounts directly — ``path("mcp/", server.urls)``, the
        ``admin.site.urls`` idiom — so the endpoints reverse within the
        namespace (``reverse("mcp:endpoint")``). Use ``async_urls`` instead
        when running under ASGI to get non-blocking dispatch for the I/O-bound
        handlers.
        """
        view = StreamableHttpViewSet.as_view(
            STREAMABLE_HTTP_ACTION_MAP,  # ty: ignore[invalid-argument-type]
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            auth_backend=self._auth_backend,
            session_store=self._session_store,
            task_store=self._task_store,
            task_executor=self._task_executor,
            # Without this a tool's ``invalidates=`` announcements are silently
            # dropped on WSGI: the viewset has nowhere to publish them, and a
            # dropped announcement looks exactly like a tool that changed nothing.
            # ``async_urls`` has always passed it.
            subscription_broker=self._subscription_broker,
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
        )
        return self._urls_with_view(view)

    @property
    def async_urls(self) -> tuple[list[URLPattern], str, str]:
        """Async URL patterns for ASGI deployments.

        The namespaced triple (like ``urls``), but ``tools/call``,
        ``resources/read`` and ``prompts/get`` dispatch through async-native
        runners. Sync collaborators (auth backend, session store, custom
        permissions) are bridged via ``asgiref.sync.sync_to_async``, so a
        fully sync stack still works; async-native ones are detected by
        signature and called directly.
        """
        view = AsyncStreamableHttpViewSet.as_view(
            ASYNC_STREAMABLE_HTTP_ACTION_MAP,
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            auth_backend=self._auth_backend,
            session_store=self._session_store,
            task_store=self._task_store,
            task_executor=self._task_executor,
            subscription_broker=self._subscription_broker,
            sse_broker=self._sse_broker,
            sse_replay_buffer=self._sse_replay_buffer,
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
        )
        return self._urls_with_view(view)

    def _urls_with_view(self, view: Any) -> tuple[list[URLPattern], str, str]:
        patterns = [
            path("", view, name="endpoint"),
            path(
                ".well-known/oauth-protected-resource",
                ProtectedResourceMetadataViewSet.as_view(
                    {"get": "list"}, auth_backend=self._auth_backend
                ),
                name="protected-resource-metadata",
            ),
        ]
        return patterns, self._url_namespace, self._url_namespace


def _template_variables(uri_template: str) -> tuple[str, ...]:
    """The ``{var}`` names in a URI template — a resource's completable arguments.

    Not shared with ``ResourceRegistry``: that one compiles templates to
    matching regexes, and this only needs the names.
    """
    return tuple(re.findall(r"\{([^}]+)\}", uri_template))


__all__ = ["MCPServer"]
