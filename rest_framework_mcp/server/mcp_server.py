from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.urls import URLPattern, path
from rest_framework.serializers import Serializer
from rest_framework_services import UNSET, UnsetType
from rest_framework_services.registry.spec_registry import SpecRegistry
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

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

    Imperative::

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

    Declarative::

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
        # Identity is resolved **once, here** — the settings read is a default
        # source for the kwargs, not a per-request lookup — so the instance is
        # the single source of truth on the wire and two servers mounted in one
        # project introduce themselves differently. ``name=None`` /
        # ``version=None`` defer to ``SERVER_INFO``, keeping the wire identity
        # of a project that configures the setting and never passes ``name=``.
        # ``icons=()`` and ``website_url=None`` mean "unset", so both defer to
        # ``SERVER_INFO`` exactly as ``name`` / ``version`` / ``title`` do —
        # passing an empty tuple is not a way to suppress configured icons.
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
        # The scalar settings, snapshotted once. Threaded to the transport and,
        # via MCPCallContext, to every handler — so nothing reads settings on
        # the request path, and two servers here can genuinely differ. Override
        # a field with ``config=build_mcp_config(page_size=500)``.
        self._config: MCPConfig = config if config is not None else build_mcp_config()
        self._url_namespace: str = url_namespace
        self._tools: ToolRegistry = ToolRegistry()
        self._resources: ResourceRegistry = ResourceRegistry()
        self._prompts: PromptRegistry = PromptRegistry()
        # ``resource_url`` configures the *default* backend. A custom backend
        # owns its own audience policy (the protocol says nothing about a
        # resource URL), so there is nowhere to forward this to — rather than
        # drop it silently and leave audience enforcement quietly unconfigured,
        # say so.
        if resource_url is not None and auth_backend is not None:
            raise ImproperlyConfigured(
                "Pass resource_url= or auth_backend=, not both — a custom auth "
                "backend owns its own audience binding. Configure it there, e.g. "
                f"auth_backend=DjangoOAuthToolkitBackend(resource_url={resource_url!r})."
            )
        # Collaborators are constructed, never resolved from a dotted path: the
        # consumer passes an object, or gets the package default built here.
        # Knowing the concrete class is what lets the session store below be
        # namespaced, and the resource URL below be handed over — a dotted-path
        # loader could only call ``cls()``.
        self._auth_backend: MCPAuthBackend = (
            auth_backend
            if auth_backend is not None
            else DjangoOAuthToolkitBackend(resource_url=resource_url)
        )
        # Namespaced by default: the cache-backed store shares one Django cache
        # across every server in the process, so without this a session minted
        # at ``/public/mcp`` satisfies ``/internal/mcp``'s ownership check and a
        # DELETE against either destroys the other's session.
        #
        # Keyed on ``name`` — the spec's programmatic identifier — and not on
        # ``url_namespace``, which is a *routing* detail. A server used only
        # in-process (``acall_tool``, the django-ag-ui bridge) is never mounted,
        # so its ``url_namespace`` is a meaningless default; keying on it would
        # collide that server with a mounted one at the default namespace even
        # though their names differ, and Django's duplicate-namespace check
        # (urls.W005) cannot see an unmounted server. Keying on identity also
        # means renaming a URL prefix doesn't silently drop every session.
        #
        # A store the consumer builds carries whatever namespace they gave it.
        self._session_store: SessionStore = (
            session_store
            if session_store is not None
            else DjangoCacheSessionStore(namespace=self.name)
        )
        # The broker is only constructed when the consumer hasn't supplied
        # one — instance state, never module-level. Multi-process deployments
        # need an out-of-process broker (Redis pub/sub etc.); single-process
        # SSE works out of the box.
        # Default to the in-process broker; consumers running multi-worker
        # ASGI deploy ``RedisSSEBroker`` (or any other ``SSEBroker``-shaped
        # impl) and pass it explicitly.
        self._sse_broker: SSEBroker = sse_broker or InMemorySSEBroker()
        # Replay buffer is opt-in: ``None`` means no resume support
        # (existing wire shape, no ``id:`` lines, ``Last-Event-ID``
        # silently ignored). Set to enable per-session bounded replay.
        self._sse_replay_buffer: SSEReplayBuffer | None = sse_replay_buffer
        # ⚠ Tasks are **off** unless both halves are present, and the store's
        # default is deliberately not "always build one": a server with a store
        # but nowhere to run the work would advertise the extension, hand out
        # handles, and never finish any of them. So the executor is the switch
        # — supply one and a cache-backed store appears (namespaced like the
        # session store, for the same reason), supply neither and nothing about
        # this server changes.
        #
        # ``task_store=None`` is distinguishable from "not passed", which is
        # what ``UNSET`` buys: passing ``None`` alongside an executor is a way
        # to say "I will wire the store later", not a request for the default.
        # ⚠ No default is constructed. The in-memory broker works only in a
        # single process, and a server that quietly got one would advertise
        # subscription support and then deliver nothing as soon as a second
        # worker existed — the failure looks exactly like "nothing ever
        # changed". Subscriptions are opt-in, and opting in means naming the
        # broker you actually want.
        self._subscription_broker: SubscriptionBroker | None = subscription_broker
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
        """Register a :class:`ServiceSpec` as an MCP **mutation** tool.

        Mirrors :meth:`register_resource`'s spec-only contract — the unit
        of registration is a ``ServiceSpec`` from
        ``djangorestframework-services``. The dispatch pipeline runs
        ``input_serializer → run_service(atomic) → output_selector? →
        output_serializer``, so this is the right surface for
        side-effecting operations (creates, updates, deletes, anything
        that wants ``transaction.atomic()``).

        For read-shaped operations (list/retrieve with optional filtering
        / ordering / pagination) use :meth:`register_selector_tool`
        instead — selectors return raw querysets and the tool layer owns
        the post-fetch pipeline.

        ``meta`` is the base protocol's generic ``_meta`` bundle: an
        open extension namespace emitted verbatim under the ``"_meta"``
        key of this tool's ``tools/list`` entry (omitted entirely when
        empty). It is *not* the ``annotations`` hint bundle — those are a
        closed, spec-defined set of client hints; ``_meta`` is where
        protocol extensions put their own keys. Passed through as given:
        this layer neither validates the keys nor reserves any.

        ``ui`` links this tool to an interactive view registered with
        :meth:`register_ui_resource`, so a host renders the result inline
        instead of showing raw JSON. The view must already be registered on
        this server, and the tool must emit ``structuredContent`` — that is
        what the view renders from — or the link is refused at registration
        rather than shipping a view that comes up blank.
        """
        ui_meta = build_ui_tool_meta(
            name=name,
            ui=ui,
            meta=meta,
            resources=self._resources,
            include_structured_content=include_structured_content,
            default_structured_content=self._config.include_structured_content,
        )
        binding = service_spec_to_tool(
            name=name,
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
            url_kwargs=tuple(url_kwargs),
            query_params=tuple(query_params),
            max_result_bytes=max_result_bytes,
            dispatch_timeout=dispatch_timeout,
        )
        check_tool_permissions_declared(
            binding.name, binding.permissions, require=self._config.require_tool_permissions
        )
        check_tool_description_present(
            binding.name, binding.description, require=self._config.require_tool_descriptions
        )
        # Resolve the structured-output pair eagerly. The combination is fully
        # known once the binding's overrides meet the server defaults, so the
        # spec-violating pairing should fail at registration — which is import
        # time — rather than on the first ``tools/call`` to reach it. Checking
        # the *globals* alone would be wrong: a server-wide "schema on, content
        # off" is legal precisely when every binding overrides it back on.
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
        """Register a :class:`SelectorSpec` as an MCP **read** tool.

        Read-shaped sibling of :meth:`register_service_tool`. The
        selector returns a raw, unscoped queryset; the tool layer owns
        the post-fetch pipeline:

        .. code-block:: text

            arguments → validate(merged inputSchema)
                      → run_selector
                      → FilterSet(data=...).qs    (if spec.filter_set set)
                      → order_by(...)             (if ordering_fields set)
                      → paginate                  (if paginate=True)
                      → output_serializer(many=True)
                      → ToolResult

        Each pipeline knob is optional. A selector tool with no
        ``spec.filter_set`` / ``ordering_fields`` / ``paginate`` set
        behaves like a plain RPC read against the selector — same
        effective contract as a service tool minus the side effects.

        Filtering is declared on the spec, not here: set
        ``SelectorSpec.filter_set`` (``djangorestframework-services``
        0.18+) and both the HTTP and MCP transports honour it. It
        requires the ``[filter]`` extra (``django-filter``); schema
        generation surfaces a clear ``ImportError`` if a spec carries a
        ``filter_set`` without the package installed. ``ordering_fields``
        / ``paginate`` stay here — they are MCP pipeline mechanics with
        no spec analogue.

        The selector's shape (``LIST`` vs ``RETRIEVE``) is read from
        ``spec.kind`` — a required field on ``SelectorSpec`` in
        ``djangorestframework-services`` 0.13+. ``LIST`` runs the full
        post-fetch pipeline (``spec.filter_set`` / ``ordering_fields`` /
        ``paginate``) and renders with ``many=True``; ``RETRIEVE``
        rejects those pipeline knobs at registration and renders the
        result with ``many=False``.

        ``meta`` is the generic ``_meta`` bundle for this tool's
        ``tools/list`` entry, and ``ui`` links it to an interactive view —
        both as on :meth:`register_service_tool`.
        """
        ui_meta = build_ui_tool_meta(
            name=name,
            ui=ui,
            meta=meta,
            resources=self._resources,
            include_structured_content=include_structured_content,
            default_structured_content=self._config.include_structured_content,
        )
        binding = selector_spec_to_tool(
            name=name,
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
            url_kwargs=tuple(url_kwargs),
            query_params=tuple(query_params),
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
        # Resolve the structured-output pair eagerly. The combination is fully
        # known once the binding's overrides meet the server defaults, so the
        # spec-violating pairing should fail at registration — which is import
        # time — rather than on the first ``tools/call`` to reach it. Checking
        # the *globals* alone would be wrong: a server-wide "schema on, content
        # off" is legal precisely when every binding overrides it back on.
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
        :class:`~rest_framework_services.registry.spec_registry.SpecRegistry`
        (``djangorestframework-services`` 0.27+) so each transport reads one
        source instead of enumerating the specs again. This is the MCP end of
        that: it walks the registry and calls
        :meth:`register_service_tool` / :meth:`register_selector_tool` per
        entry, discriminating on the spec type.

        It is a **source for** this server's own ``ToolRegistry``, not a
        replacement for it — every tool still lands as a normal binding, and
        names still share the one tool namespace (a collision raises, as
        always). The spec registry carries only what is invariant across
        transports (which spec, its canonical name, its tags); every MCP knob
        stays here, per tool, via ``overrides``::

            server.register_specs(
                registry.by_tag("public"),
                overrides={
                    "list_orders": {"paginate": True, "ordering_fields": ["created_at"]},
                    "refund_order": {"annotations": {"destructiveHint": True}},
                },
            )

        ``overrides`` maps a registered name to the keyword arguments handed to
        that entry's registration method. It stays a plain mapping rather than
        a dataclass because the two methods take different knobs — a single
        record would duplicate both signatures and drift from them. The keys
        are therefore checked against each method's own signature, which means
        a knob used on the wrong spec kind (``paginate`` on a ``ServiceSpec``)
        raises :exc:`TypeError` from that method. An ``overrides`` key naming a
        spec the registry doesn't hold raises :exc:`ValueError` here — that is
        a typo, not an intentional no-op.

        Registration is not transactional: a failure partway leaves the
        earlier entries registered. That is harmless in the intended use —
        registration happens at configuration time, so a raise aborts startup
        anyway.

        Returns the bindings in registration order, mirroring the per-tool
        methods that each return theirs.
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
        ui: UIToolMeta | None = None,
        include_structured_content: bool | None = None,
        include_output_schema: bool | None = None,
        unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
        always_listed: bool = False,
        max_result_bytes: int | None | UnsetType = UNSET,
        dispatch_timeout: float | None | UnsetType = UNSET,
    ) -> ChainToolBinding:
        """Register an ordered sequence of specs as a single MCP tool.

        Each :class:`~rest_framework_mcp.registry.types.chain_step.ChainStep`
        wraps a ``ServiceSpec`` (write) or ``SelectorSpec`` (read) and binds
        its result to an alias. A step's ``inputs`` callable reads the
        validated tool arguments (``ctx.args``) and any prior step's output
        (``ctx[alias]``) to build that step's call kwargs — so one tool call
        can express ``retrieve x → write y → write z`` with ``z`` derived
        from both ``x`` and ``y``.

        ``atomic=True`` (the default) runs the whole sequence inside one
        ``transaction.atomic()``: any step raising a
        ``ServiceError`` / ``ServiceValidationError`` rolls back every prior
        write and the JSON-RPC error carries ``failedStep``.

        The advertised ``inputSchema`` is ``input_serializer`` when set,
        otherwise the first step's serializer (the first-step fallback). The
        response is the ``output_alias`` step's rendered output (default: the
        last step), or ``{alias: rendered}`` for every serializer-bearing
        step when ``output_all=True``.

        Each step's ``spec.permission_classes`` are AND-combined with the
        chain-level ``permissions`` and evaluated up front — a failing step
        permission blocks the whole chain before any step runs.

        Chains deliberately do not run the selector post-fetch pipeline
        (filter / order / paginate); for that, expose the selector as its
        own :meth:`register_selector_tool`.

        ``meta`` is the generic ``_meta`` bundle for this tool's
        ``tools/list`` entry, and ``ui`` links it to an interactive view —
        both as on :meth:`register_service_tool`.
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
        # Resolve the structured-output pair eagerly. The combination is fully
        # known once the binding's overrides meet the server defaults, so the
        # spec-violating pairing should fail at registration — which is import
        # time — rather than on the first ``tools/call`` to reach it. Checking
        # the *globals* alone would be wrong: a server-wide "schema on, content
        # off" is legal precisely when every binding overrides it back on.
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

        The blessed, transport-neutral entry point: hand a tool ``name`` and a
        flat ``arguments`` dict (the role ``request.data`` / query params play on
        HTTP) plus the acting ``user``, and get back the same :class:`ToolResult`
        the wire handlers build — without going through JSON-RPC. An in-process
        consumer (the django-ag-ui bridge, a Pydantic-AI toolset, a management
        command) calls this instead of re-implementing dispatch.

        Built on the sister repo's ``dispatch_spec`` / ``render_spec_output`` /
        ``enforce_permissions``, so the spec core (instance resolution, input
        validation, the service / selector run, the output-selector re-fetch,
        queryset shaping incl. ``filter_set``, and the retrieve nullability
        contract) is shared with every other transport rather than reproduced.

        This is the spec core only: it honours the binding's ``argument_binding`` /
        ``unknown_arguments`` policies and the spec's ``permission_classes``
        (object-level checks included), but does not layer on the read-shaped
        transport extras — pagination, ordering, and a selector binding's MCP-only
        ``input_serializer`` — nor the transport-level MCP permissions / rate
        limits. For those (and for tool listing), use the full in-process transport
        surface, :meth:`acall_tool` / :meth:`list_tools`. Chain tools are
        unsupported — they orchestrate several specs and raise :class:`TypeError`.

        Raises :class:`KeyError` when no tool is registered under ``name``.
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

        The in-process twin of a ``tools/list`` request: returns one page of the
        tool catalog with the *same* merged ``inputSchema`` the HTTP transport
        advertises (serializer fields plus a selector tool's filter / ordering /
        pagination arguments and the ``additionalProperties`` policy), the same
        per-caller listing-permission filter (``FILTER_LISTINGS_BY_PERMISSIONS``),
        and the same opaque-cursor pagination — pass the returned ``nextCursor``
        back to fetch the next page. A :class:`JsonRpcError` signals a bad cursor.

        ``scopes`` are the caller's granted scopes; pass them so a
        ``ScopeRequired``-gated tool is visible under
        ``FILTER_LISTINGS_BY_PERMISSIONS`` exactly as it would be on the wire.

        Unlike :meth:`call_tool` (the spec core), this is the full transport
        surface — the entry point for an in-process consumer (the django-ag-ui
        bridge, a Pydantic-AI toolset) that must mirror what a remote MCP client
        would see. Under an event loop use :meth:`alist_tools` — a listing
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
        """Async :meth:`list_tools` — safe to call from an event loop.

        Listing itself is pure Python, but the per-caller permission filter
        (``FILTER_LISTINGS_BY_PERMISSIONS``) may run a DB-backed check (e.g.
        ``DjangoPermRequired`` → ``user.has_perm``), which raises
        ``SynchronousOnlyOperation`` when reached synchronously from within an
        event loop — the exact context an async in-process consumer runs in. The
        whole sync handler therefore runs in Django's thread-sensitive executor.
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

        The in-process twin of a ``tools/call`` request: routes through the same
        async handler the wire uses, so the transport-level MCP permissions and
        rate limits, the selector post-fetch pipeline (filter / order / paginate),
        a selector binding's MCP-only ``input_serializer``, chain tools, and the
        output format all apply — everything :meth:`call_tool` (the spec core)
        deliberately omits. Returns the wire's result payload (a ``dict`` carrying
        ``content`` / ``structuredContent`` / ``isError``), or a
        :class:`JsonRpcError` for a protocol fault (unknown tool, malformed
        ``arguments`` shape, denied permission).

        ``arguments`` is the flat dict that ``request.data`` / query params play on
        HTTP; ``user`` is the acting user and ``request`` the originating Django
        request when there is one (a minimal request is synthesised otherwise,
        mirroring :meth:`call_tool`). ``scopes`` are the caller's granted scopes,
        populating the synthetic token so a ``ScopeRequired``-gated tool is
        invokable in-process just as it is on the wire.
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

        Carries the acting ``user`` (as both ``request.user`` and the synthetic
        :class:`TokenInfo`) plus the server's registries. When ``request`` is
        ``None`` a minimal :class:`~django.http.HttpRequest` is synthesised
        bearing the user — the shape ``build_offline_context`` uses for the
        spec-core path — so permission classes reading ``request.user`` behave as
        they would on HTTP. The protocol version is the server's first (most
        preferred) supported version, not a hardcoded literal.

        ``scopes`` populate the synthetic :class:`TokenInfo`, so a scope-gated
        tool (``ScopeRequired``) is callable and listable in-process the same way
        it is over the wire; the default (``None``) is an empty scope set.
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
        surface of the worker side::

            @shared_task
            def run_mcp_task(task_id: str) -> None:
                my_server.run_task(task_id)

        Everything it needs comes out of the store: the tool, the arguments,
        and the authorization context to re-check them under. Nothing is
        returned — the client learns the outcome by polling ``tasks/get``.

        Safe to call for an id that is unknown, already claimed or already
        finished: each is a no-op. That matters because queues deliver at least
        once, and a retried delivery must not run a mutation twice.

        Raises if the server has no task store, because a worker calling this
        on a server that cannot run tasks is a wiring mistake that would
        otherwise fail as silence — the job would "succeed" and the client
        would poll a handle forever.
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

        Rebuilt rather than remembered — there is no request left to carry, and
        a serialised one would be a stale copy of a live object. The identity
        half comes back out of the record (see ``build_worker_token``); the
        registries, config and stores come from this server, which is why the
        factory lives here and not in the task module.

        ``client_capabilities`` is deliberately empty: the worker is not
        serving a client, and a task must never create another task. An empty
        declaration makes that structural rather than a rule someone has to
        remember — ``maybe_create_task`` refuses, so a ``REQUIRED`` binding
        reached this way fails visibly instead of queueing itself forever.
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
        job, a ``save()`` override, a signal handler you wrote yourself::

            await server.notify_resource_updated(f"invoices://{invoice.pk}")

        Returns how many subscribers were reached, which is a diagnostic rather
        than a guarantee: ``0`` means nobody was listening, which is the
        ordinary case and not an error. Notifications are best-effort by design
        — a client that misses one re-reads the resource.

        ⚠ **A URI, not a template.** Publish the concrete URI that changed, and
        publish the collection URI too if you want watchers of the collection to
        hear about it — matching is exact, deliberately (see
        :func:`~rest_framework_mcp.subscriptions.utils.topic_for_resource`).

        ⚠ **Publish after the transaction commits.** Inside
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

        A server with no broker is not misconfigured — it simply does not push —
        so publishing is a no-op rather than an error. That keeps
        ``notify_resource_updated`` safe to call unconditionally from a service,
        which is what makes it usable as the write path's own trigger.
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
        """Register a :class:`SelectorSpec` as an MCP resource.

        The unit of registration is a spec, mirroring :meth:`register_service_tool`'s
        :class:`ServiceSpec` requirement. ``selector.selector`` is the
        callable dispatched at ``resources/read`` time;
        ``selector.output_serializer`` fills in when the caller didn't pass
        one explicitly (the explicit ``output_serializer=`` kwarg wins);
        ``selector.kwargs`` becomes the binding's per-request kwargs
        provider.

        Bare callables are no longer accepted at this surface — wrap them in
        ``SelectorSpec(selector=fn)``, or use :meth:`resource` (the decorator
        form), which wraps the function automatically.

        The shape (``LIST`` vs ``RETRIEVE``) is read from
        ``selector.kind`` and drives the ``many=`` flag on
        ``output_serializer`` at dispatch. ``RETRIEVE`` is the typical
        case for a URI-template lookup.

        ``meta`` is the generic ``_meta`` bundle (see
        :meth:`register_service_tool`) for this resource's listing entry —
        ``resources/list`` for a concrete URI, ``resources/templates/list``
        for a template — and for the ``contents`` block ``resources/read``
        returns.

        ``encoding`` decides how the selector's value becomes the read body:
        ``JSON`` (the default) pretty-prints it, ``TEXT`` returns it verbatim.
        Anything whose ``mime_type`` is not JSON — Markdown, CSV, plain text —
        wants ``TEXT``, or the document comes back wrapped in a quoted string
        literal. For an HTML view use :meth:`register_ui_resource`, which sets
        both.
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
        # Template variables are the only completable arguments a resource has.
        check_completions_declared(
            f"Resource {name!r}", binding.completions, _template_variables(uri_template)
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
        inside a sandboxed iframe it constructs itself. This server's whole job
        is to *declare*: serve the document, and describe what it needs in
        ``_meta``. The iframe, the CSP enforcement and the ``ui/*`` postMessage
        bridge are the host's, and are deliberately not implemented here.

        Give exactly one content source — ``template_name`` (a Django template,
        the idiomatic choice), ``html`` (a literal document), or ``selector``
        (a zero-argument callable returning one).

        **Keep tenant data out of the view.** Hosts may prefetch and cache a
        view before any tool call, so it is a shell that hydrates itself at
        runtime from tool results — which is also why the template renders with
        no context. This is a house rule rather than a spec rule, and it is the
        one thing a Django author's instinct gets wrong, because rendering the
        queryset into the template is normally the right answer.

        ``ui=`` is the typed :class:`UIResourceMeta` — CSP origins, browser
        permissions, publisher ``domain``, border preference — which serialises
        into ``_meta`` under the extension's key. ``meta=`` remains available
        for *other* extensions; passing both ``ui=`` and that same key inside
        ``meta=`` raises, rather than letting one silently win.

        The result is an ordinary :class:`ResourceBinding`, so it shares one
        URI namespace with data resources (a collision raises as always),
        appears in ``resources/list``, and honours ``permissions`` /
        ``always_listed``. Views default to **unguarded** — the MCP session is
        already authenticated and a view is a static asset, not tenant data.
        """
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

        ``render`` receives the prompt arguments as kwargs (plus ``request``
        and ``user`` if it declares them) and returns either a string, a
        list of strings, a list of :class:`PromptMessage`, or a coroutine
        yielding any of those — the dispatch layer normalises the result.

        ``meta`` is the generic ``_meta`` bundle for this prompt's
        ``prompts/list`` entry — see :meth:`register_service_tool`.
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
        """Decorator form of :meth:`register_service_tool`.

        If ``spec`` is supplied it is used verbatim; otherwise a
        :class:`ServiceSpec` is constructed from the keyword arguments.
        The original function is returned unchanged so it remains
        callable from Python without going through the MCP transport.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            # Sister-repo 0.13+ collapsed the flat output fields under
            # ``output_selector_spec``. Build the nested spec lazily so a
            # decorator with no output-side declarations doesn't pay the
            # cost of an empty RetrieveSelector envelope.
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
        """Decorator form of :meth:`register_selector_tool`.

        If ``spec`` is supplied it is used verbatim; otherwise a
        :class:`SelectorSpec` is constructed from the wrapped function
        and the keyword arguments. The original function is returned
        unchanged so it remains callable from Python without going
        through the MCP transport.

        ``kind`` is required when ``spec`` is omitted (the decorator
        auto-constructs a :class:`SelectorSpec` and the spec's own
        ``kind`` field is mandatory). When ``spec`` is supplied,
        ``kind`` is read from ``spec.kind`` and any value passed here
        is ignored.
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
        :class:`SelectorSpec` is constructed from the wrapped function and
        the keyword arguments. The original function is returned unchanged
        so it remains callable from Python without going through the MCP
        transport.

        ``kind`` is required when ``spec`` is omitted; otherwise it
        comes from ``spec.kind`` and any value passed here is ignored.
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

        Returns ``True`` if a subscriber was present, ``False`` if no client
        is currently connected. Most callers will fire-and-forget — a missed
        push is not generally an error, since clients can pull state via
        ``tools/call`` round-trips. The broker enforces single-subscriber
        semantics: re-subscribing replaces the old queue silently.

        When a :class:`SSEReplayBuffer` is configured the payload is
        recorded *before* publishing so that:

        - The published frame carries an event ID the SSE generator emits
          on the wire (``id: <id>\\ndata: <payload>\\n\\n``).
        - A subsequent reconnect with ``Last-Event-ID`` can drain the
          missed events from the buffer before resuming live mode.

        Without a buffer the wire shape is unchanged (no ``id:`` lines)
        and resume is disabled.

        Multi-process deployments need an out-of-process broker (e.g. Redis
        pub/sub) to fan out across worker processes; the in-process broker
        only sees its own worker.
        """
        if self._sse_replay_buffer is None:
            return await self._sse_broker.publish(session_id, payload)
        event_id: str = await self._sse_replay_buffer.record(session_id, payload)
        # Wrap so the SSE response generator can emit ``id:`` alongside
        # ``data:``. The unwrap happens in ``stream_events``; broker
        # implementations are agnostic to the wrapper shape.
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
        namespace (``reverse("mcp:endpoint")``). Use :attr:`async_urls` instead
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
            server_info=self._server_info,
            instructions=self.description,
            config=self._config,
        )
        return self._urls_with_view(view)

    @property
    def async_urls(self) -> tuple[list[URLPattern], str, str]:
        """Async URL patterns for ASGI deployments.

        The namespaced triple (like :attr:`urls`), but ``tools/call``,
        ``resources/read``, and ``prompts/get`` dispatch through async-native
        runners; sync collaborators (auth backend, session store, custom
        permissions) are bridged via :func:`asgiref.sync.sync_to_async` so a
        fully sync stack still works. Async-native backends are detected by
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

    Duplicated pattern rather than imported from ``ResourceRegistry``: that
    one compiles templates to matching regexes, and this only needs the names.
    """
    return tuple(re.findall(r"\{([^}]+)\}", uri_template))


__all__ = ["MCPServer"]
