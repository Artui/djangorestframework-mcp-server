from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.urls import URLPattern, path
from django.utils.module_loading import import_string
from rest_framework.serializers import Serializer
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.adapters.selector_to_resource import selector_to_resource
from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool
from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool
from rest_framework_mcp.auth.auth_backend import MCPAuthBackend
from rest_framework_mcp.auth.protected_resource_metadata import ProtectedResourceMetadataView
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.protocol.prompt_argument import PromptArgument
from rest_framework_mcp.registry.prompt_binding import PromptBinding
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_binding import ResourceBinding
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.tool_binding import ToolBinding
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.async_streamable_http_view import AsyncStreamableHttpView
from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker
from rest_framework_mcp.transport.session_store import SessionStore
from rest_framework_mcp.transport.sse_broker import SSEBroker
from rest_framework_mcp.transport.sse_replay_buffer import SSEReplayBuffer
from rest_framework_mcp.transport.streamable_http_view import StreamableHttpView


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

    Mount the URLs in your URL conf:

        urlpatterns = [path("mcp/", include(server.urls))]
    """

    def __init__(
        self,
        *,
        name: str = "djangorestframework-mcp-server",
        description: str | None = None,
        auth_backend: MCPAuthBackend | None = None,
        session_store: SessionStore | None = None,
        sse_broker: SSEBroker | None = None,
        sse_replay_buffer: SSEReplayBuffer | None = None,
    ) -> None:
        self.name: str = name
        self.description: str | None = description
        self._tools: ToolRegistry = ToolRegistry()
        self._resources: ResourceRegistry = ResourceRegistry()
        self._prompts: PromptRegistry = PromptRegistry()
        self._auth_backend: MCPAuthBackend = auth_backend or _load_default(
            "AUTH_BACKEND", MCPAuthBackend
        )
        self._session_store: SessionStore = session_store or _load_default(
            "SESSION_STORE", SessionStore
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

    # ----- imperative registration -----

    def register_service_tool(
        self,
        *,
        name: str,
        spec: ServiceSpec,
        description: str | None = None,
        title: str | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
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
        """
        binding = service_spec_to_tool(
            name=name,
            spec=spec,
            description=description,
            title=title,
            output_format=OutputFormat.coerce(output_format),
            permissions=tuple(permissions or ()),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
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
        input_serializer: type | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        filter_set: Any | None = None,
        ordering_fields: list[str] | tuple[str, ...] | None = None,
        paginate: bool = False,
    ) -> SelectorToolBinding:
        """Register a :class:`SelectorSpec` as an MCP **read** tool.

        Read-shaped sibling of :meth:`register_service_tool`. The
        selector returns a raw, unscoped queryset; the tool layer owns
        the post-fetch pipeline:

        .. code-block:: text

            arguments → validate(merged inputSchema)
                      → run_selector
                      → FilterSet(data=...).qs    (if filter_set set)
                      → order_by(...)             (if ordering_fields set)
                      → paginate                  (if paginate=True)
                      → output_serializer(many=True)
                      → ToolResult

        Each pipeline knob is optional. A selector tool with none of
        ``filter_set`` / ``ordering_fields`` / ``paginate`` set behaves
        like a plain RPC read against the selector — same effective
        contract as a service tool minus the side effects.

        ``filter_set`` requires the ``[filter]`` extra
        (``django-filter``). The constructor surfaces a clear
        ``ImportError`` if you set it without the package installed.
        """
        binding = selector_spec_to_tool(
            name=name,
            spec=spec,
            description=description,
            title=title,
            input_serializer=input_serializer,
            output_format=OutputFormat.coerce(output_format),
            permissions=tuple(permissions or ()),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
            filter_set=filter_set,
            ordering_fields=tuple(ordering_fields or ()),
            paginate=paginate,
        )
        self._tools.register(binding)
        return binding

    def register_resource(
        self,
        *,
        name: str,
        uri_template: str,
        selector: SelectorSpec,
        description: str | None = None,
        title: str | None = None,
        output_serializer: type | None = None,
        mime_type: str = "application/json",
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
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
        """
        binding = selector_to_resource(
            name=name,
            uri_template=uri_template,
            selector=selector,
            description=description,
            title=title,
            output_serializer=output_serializer,
            mime_type=mime_type,
            permissions=tuple(permissions or ()),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations,
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
        arguments: list[PromptArgument] | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> PromptBinding:
        """Register a render callable as an MCP prompt.

        ``render`` receives the prompt arguments as kwargs (plus ``request``
        and ``user`` if it declares them) and returns either a string, a
        list of strings, a list of :class:`PromptMessage`, or a coroutine
        yielding any of those — the dispatch layer normalises the result.
        """
        binding = PromptBinding(
            name=name,
            description=description,
            title=title,
            render=render,
            arguments=tuple(arguments or ()),
            permissions=tuple(permissions or ()),
            rate_limits=tuple(rate_limits or ()),
            annotations=annotations or {},
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
        output_format: OutputFormat | str = OutputFormat.JSON,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register_service_tool`.

        If ``spec`` is supplied it is used verbatim; otherwise a
        :class:`ServiceSpec` is constructed from the keyword arguments.
        The original function is returned unchanged so it remains
        callable from Python without going through the MCP transport.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            effective_spec: ServiceSpec = spec or ServiceSpec(
                service=fn,
                input_serializer=input_serializer,
                output_serializer=output_serializer,
                output_selector=output_selector,
                atomic=atomic,
                success_status=success_status,
            )
            self.register_service_tool(
                name=name,
                spec=effective_spec,
                description=description or fn.__doc__,
                title=title,
                output_format=output_format,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
            )
            return fn

        return wrap

    def selector_tool(
        self,
        *,
        name: str,
        spec: SelectorSpec | None = None,
        input_serializer: type | None = None,
        output_serializer: type[Serializer] | None = None,
        description: str | None = None,
        title: str | None = None,
        output_format: OutputFormat | str = OutputFormat.JSON,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
        filter_set: Any | None = None,
        ordering_fields: list[str] | tuple[str, ...] | None = None,
        paginate: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register_selector_tool`.

        If ``spec`` is supplied it is used verbatim; otherwise a
        :class:`SelectorSpec` is constructed from the wrapped function
        and the keyword arguments. The original function is returned
        unchanged so it remains callable from Python without going
        through the MCP transport.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            effective_spec: SelectorSpec = spec or SelectorSpec(
                selector=fn,
                output_serializer=output_serializer,
            )
            self.register_selector_tool(
                name=name,
                spec=effective_spec,
                description=description or fn.__doc__,
                title=title,
                input_serializer=input_serializer,
                output_format=output_format,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
                filter_set=filter_set,
                ordering_fields=ordering_fields,
                paginate=paginate,
            )
            return fn

        return wrap

    def resource(
        self,
        *,
        uri_template: str,
        name: str | None = None,
        spec: SelectorSpec | None = None,
        description: str | None = None,
        title: str | None = None,
        output_serializer: type[Serializer] | None = None,
        mime_type: str = "application/json",
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: register the wrapped callable as a resource.

        If ``spec`` is supplied it is used verbatim; otherwise a
        :class:`SelectorSpec` is constructed from the wrapped function and
        the keyword arguments. The original function is returned unchanged
        so it remains callable from Python without going through the MCP
        transport.
        """

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            effective_spec: SelectorSpec = spec or SelectorSpec(
                selector=fn, output_serializer=output_serializer
            )
            self.register_resource(
                name=name or getattr(fn, "__name__", "resource"),
                uri_template=uri_template,
                selector=effective_spec,
                description=description or fn.__doc__,
                title=title,
                output_serializer=output_serializer,
                mime_type=mime_type,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
            )
            return fn

        return wrap

    def prompt(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        title: str | None = None,
        arguments: list[PromptArgument] | None = None,
        permissions: list[Any] | None = None,
        rate_limits: list[Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: register the wrapped callable as a prompt."""

        def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register_prompt(
                name=name or getattr(fn, "__name__", "prompt"),
                render=fn,
                description=description or fn.__doc__,
                title=title,
                arguments=arguments,
                permissions=permissions,
                rate_limits=rate_limits,
                annotations=annotations,
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
    def urls(self) -> list[URLPattern]:
        """Sync URL patterns. Suitable for any deployment (WSGI or ASGI).

        Use :attr:`async_urls` instead when running under ASGI to get
        non-blocking dispatch for the I/O-bound handlers.
        """
        view = StreamableHttpView.as_view(
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            auth_backend=self._auth_backend,
            session_store=self._session_store,
        )
        return self._urls_with_view(view)

    @property
    def async_urls(self) -> list[URLPattern]:
        """Async URL patterns for ASGI deployments.

        ``tools/call``, ``resources/read``, and ``prompts/get`` dispatch
        through async-native runners; sync collaborators (auth backend,
        session store, custom permissions) are bridged via
        :func:`asgiref.sync.sync_to_async` so a fully sync stack still works.
        Async-native backends are detected by signature and called directly.
        """
        view = AsyncStreamableHttpView.as_view(
            tools=self._tools,
            resources=self._resources,
            prompts=self._prompts,
            auth_backend=self._auth_backend,
            session_store=self._session_store,
            sse_broker=self._sse_broker,
            sse_replay_buffer=self._sse_replay_buffer,
        )
        return self._urls_with_view(view)

    def _urls_with_view(self, view: Any) -> list[URLPattern]:
        return [
            path("", view, name="mcp-endpoint"),
            path(
                ".well-known/oauth-protected-resource",
                ProtectedResourceMetadataView.as_view(auth_backend=self._auth_backend),
                name="mcp-protected-resource-metadata",
            ),
        ]


def _load_default(setting_name: str, expected: type) -> Any:
    dotted: str = get_setting(setting_name)
    cls = import_string(dotted)
    instance: Any = cls()
    if not isinstance(instance, expected):  # pragma: no cover - defensive
        raise TypeError(
            f"Configured {setting_name} {dotted!r} does not implement {expected.__name__}"
        )
    return instance


__all__ = ["MCPServer"]
