from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from django.http import HttpResponse, HttpResponseBase, JsonResponse
from django.utils.decorators import classonlymethod
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.viewsets import ViewSet

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.auth.types.auth_backend import MCPAuthBackend
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.async_dispatch import adispatch
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.parse_message import parse_message
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.types.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.negotiate_protocol_version import negotiate_protocol_version
from rest_framework_mcp.transport.origin_validation import is_origin_allowed
from rest_framework_mcp.transport.sse_response import build_sse_response
from rest_framework_mcp.transport.types.session_store import SessionStore
from rest_framework_mcp.transport.types.sse_broker import SSEBroker
from rest_framework_mcp.transport.types.sse_replay_buffer import SSEReplayBuffer

_SESSION_HEADER: str = "Mcp-Session-Id"
_VERSION_HEADER: str = "Mcp-Protocol-Version"


def _error_response(
    *, code: int, message: str, data: Any = None, status: int = 400, request_id: Any = None
) -> JsonResponse:
    body: dict[str, Any] = JsonRpcResponse(
        id=request_id, error=JsonRpcError(code=code, message=message, data=data)
    ).to_dict()
    return JsonResponse(body, status=status)


class AsyncStreamableHttpViewSet(ViewSet):
    """Async sibling of :class:`StreamableHttpViewSet` for ASGI deployments.

    Wire behaviour matches the sync ViewSet for POST and DELETE — same
    headers, same status codes, same JSON-RPC shapes. The async path
    additionally supports server-initiated SSE on GET when an
    :class:`SSEBroker` is wired in; without one, GET returns 405 (the
    spec explicitly allows this when the server has nothing to push).

    The async ViewSet dispatches I/O-bound handlers via
    :func:`arun_service` / :func:`arun_selector` and bridges sync
    collaborators (auth backend, session store, permissions) through
    :func:`asgiref.sync.sync_to_async` so a fully sync stack still works
    correctly under ASGI.

    DRF's default authentication / permission / throttling layers are
    disabled for the same reasons :class:`StreamableHttpViewSet` skips
    them — MCP's auth pipeline is bespoke. Wired via
    ``AsyncStreamableHttpViewSet.as_view(ASYNC_STREAMABLE_HTTP_ACTION_MAP,
    ...)``.
    """

    authentication_classes: tuple = ()
    permission_classes = (AllowAny,)
    renderer_classes = (JSONRenderer,)
    view_is_async: bool = True

    tools: ToolRegistry | None = None
    resources: ResourceRegistry | None = None
    prompts: PromptRegistry | None = None
    auth_backend: MCPAuthBackend | None = None
    session_store: SessionStore | None = None
    sse_broker: SSEBroker | None = None
    sse_replay_buffer: SSEReplayBuffer | None = None

    @classonlymethod
    def as_view(cls, actions: Any = None, **initkwargs: Any) -> Any:  # type: ignore[override]
        """Wrap DRF's sync ``as_view`` so Django routes the callable as async.

        DRF's ``ViewSet.as_view`` returns a sync function that calls
        ``self.dispatch(request)``. Because our ``dispatch`` is
        ``async def``, that sync wrapper returns an unawaited coroutine
        — Django's request handler then raises ``ValueError`` about an
        unawaited coroutine. Wrapping in an async function and tagging
        it with the ``_is_coroutine`` marker Django's ``View.as_view``
        uses tells the request handler to ``await`` the view.
        """
        sync_view = super().as_view(actions=actions, **initkwargs)

        async def async_view(request: Any, *args: Any, **kwargs: Any) -> HttpResponseBase:
            # ``sync_view`` returns whatever ``self.dispatch`` returns; our
            # overridden ``dispatch`` is async, so this is a coroutine the
            # DRF stub doesn't know about. Cast to ``Any`` to bypass the
            # stub's ``Response`` return-type narrowing.
            return await cast(Any, sync_view(request, *args, **kwargs))

        # Copy DRF's ViewSet introspection attributes so downstream code
        # (URL reversing, test introspection of ``view_initkwargs``) keeps
        # working through the async wrapper.
        for attr in ("view_class", "view_initkwargs", "cls", "actions", "initkwargs"):
            if hasattr(sync_view, attr):
                setattr(async_view, attr, getattr(sync_view, attr))
        # Tag as a coroutine so ``django.core.handlers.base`` awaits it.
        # ``asyncio.coroutines._is_coroutine`` is Django's documented marker
        # for "this callable returns a coroutine"; the underscore-prefixed
        # name is a private asyncio implementation detail (not part of the
        # public API), but Django relies on it stably across versions.
        async_view._is_coroutine = (  # ty: ignore[unresolved-attribute]
            asyncio.coroutines._is_coroutine  # ty: ignore[unresolved-attribute]
        )
        return async_view

    async def dispatch(  # ty: ignore[invalid-method-override]
        self, request: Any, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Async-aware mirror of :meth:`rest_framework.views.APIView.dispatch`.

        DRF's stock ``APIView.dispatch`` is sync — it calls
        ``handler(request)`` and passes whatever comes back to
        ``finalize_response``. With ``async def`` action methods that
        return coroutines, ``finalize_response`` chokes because it
        ``isinstance``-checks against ``HttpResponseBase``. Awaiting
        the coroutine here closes the gap.

        DRF 3.16+ added some async hooks at the framework edges
        (permissions, throttles) but the ViewSet dispatch path itself
        still assumes sync. Until that lands upstream, this override
        is the lightest-weight workaround that lets us return real
        async-native responses without the ``async_to_sync`` thread-hop
        that would defeat the point of an async transport.
        """
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        try:
            self.initial(request, *args, **kwargs)
            # ``request.method`` is typed as ``str | None`` on the DRF stub
            # because ``HttpRequest.method`` is theoretically Optional —
            # but at this point we're inside an HTTP request flow, so
            # ``method`` is always populated.
            method: str = request.method.lower()  # ty: ignore[unresolved-attribute]
            handler = getattr(self, method, self.http_method_not_allowed)
            result = handler(request, *args, **kwargs)
            # Action handlers are normally async (``handle_jsonrpc`` etc.)
            # but ``http_method_not_allowed`` from the base class is sync.
            # Accept either shape so a stray sync handler doesn't 500.
            response = await result if asyncio.iscoroutine(result) else result
        except Exception as exc:
            response = self.handle_exception(exc)

        # DRF's ``finalize_response`` is typed ``response: Response`` on
        # the stub but accepts ``HttpResponseBase`` at runtime (it
        # short-circuits for non-Response shapes). Our actions return
        # Django ``HttpResponse`` / ``JsonResponse`` deliberately. Cast
        # to ``Any`` at the boundary to bypass the over-narrow stub.
        self.response = self.finalize_response(request, cast(Any, response), *args, **kwargs)
        return self.response

    # ----- DRF async action methods -----

    async def handle_jsonrpc(self, request: Request) -> HttpResponse:
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard

        max_bytes: int = int(get_setting("MAX_REQUEST_BYTES"))
        if len(http_request.body) > max_bytes:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Request body too large",
                status=413,
            )

        try:
            payload: Any = json.loads(http_request.body or b"null")
        except json.JSONDecodeError as exc:
            return _error_response(
                code=JsonRpcErrorCode.PARSE_ERROR, message=f"Invalid JSON: {exc.msg}"
            )

        try:
            message = parse_message(payload)
        except ValueError as exc:
            return _error_response(code=JsonRpcErrorCode.INVALID_REQUEST, message=str(exc))

        is_initialize: bool = isinstance(message, JsonRpcRequest) and message.method == "initialize"

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        negotiated: str | None = negotiate_protocol_version(
            version_header, is_initialize=is_initialize
        )
        if negotiated is None:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
                request_id=getattr(message, "id", None),
            )
        protocol_version: str = negotiated

        store = self._require_session_store()
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if not is_initialize and (not session_id or not await acall(store.exists, session_id)):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Unknown or missing MCP-Session-Id",
                status=404,
                request_id=getattr(message, "id", None),
            )

        backend = self._require_auth_backend()
        token = await acall(backend.authenticate, http_request)
        if token is None:
            challenge: str = backend.www_authenticate_challenge(error="invalid_token")
            response = JsonResponse(
                {"error": "unauthorized", "error_description": "Authentication required."},
                status=401,
            )
            response["WWW-Authenticate"] = challenge
            return response

        context = MCPCallContext(
            http_request=http_request,
            token=token,
            tools=self._require_tools(),
            resources=self._require_resources(),
            prompts=self._require_prompts(),
            protocol_version=protocol_version,
            session_id=session_id,
        )

        if isinstance(message, JsonRpcNotification):
            return HttpResponse(status=202)

        if not isinstance(message, JsonRpcRequest):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST, message="Expected a JSON-RPC request"
            )

        result: Any = await adispatch(message.method, _params_dict(message.params), context)

        if isinstance(result, JsonRpcError):
            response_body = JsonRpcResponse(id=message.id, error=result).to_dict()
        else:
            response_body = JsonRpcResponse(id=message.id, result=result).to_dict()
        http_response = JsonResponse(response_body, status=200)

        if is_initialize and not isinstance(result, JsonRpcError):
            new_session: str = await acall(store.create)
            http_response[_SESSION_HEADER] = new_session
        return http_response

    async def handle_get(self, request: Request) -> HttpResponseBase:
        """GET action: open a server-pushed SSE stream for the current session.

        Spec compliance:

        - 405 if no broker is configured (server has nothing to push).
        - 400 if the protocol-version header is missing/unsupported
          (parity with POST — the spec is silent on GET version handling,
          but consistent enforcement avoids surprising behaviour).
        - 404 if the supplied session id is unknown.
        - Otherwise: ``text/event-stream`` with idle keep-alives and any
          payloads ``MCPServer.notify`` enqueues.
        """
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard

        if self.sse_broker is None:
            return HttpResponse(status=405)

        version_header: str | None = http_request.headers.get(_VERSION_HEADER)
        if negotiate_protocol_version(version_header, is_initialize=False) is None:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
            )

        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        store = self._require_session_store()
        if not session_id or not await acall(store.exists, session_id):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Unknown or missing MCP-Session-Id",
                status=404,
            )

        # ``Last-Event-ID`` is only honored when a replay buffer is wired in;
        # otherwise we silently ignore it (no buffered events to replay).
        last_event_id: str | None = (
            http_request.headers.get("Last-Event-ID")
            if self.sse_replay_buffer is not None
            else None
        )
        return build_sse_response(
            self.sse_broker,
            session_id,
            replay_buffer=self.sse_replay_buffer,
            last_event_id=last_event_id,
        )

    async def terminate_session(self, request: Request) -> HttpResponse:
        http_request = request._request  # noqa: SLF001
        guard: HttpResponse | None = self._check_origin(http_request)
        if guard is not None:
            return guard
        session_id: str | None = http_request.headers.get(_SESSION_HEADER)
        if session_id:
            # Drop replay history first; the session is going away and any
            # buffered events would never be delivered.
            if self.sse_replay_buffer is not None:
                await self.sse_replay_buffer.forget(session_id)
            await acall(self._require_session_store().destroy, session_id)
        return HttpResponse(status=204)

    # ----- collaborator accessors -----

    def _check_origin(self, request: Any) -> HttpResponse | None:
        origin: str | None = request.headers.get("Origin")
        if not is_origin_allowed(origin):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message=f"Origin not allowed: {origin!r}",
                status=403,
            )
        return None

    def _require_tools(self) -> ToolRegistry:
        if self.tools is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpViewSet is missing a SessionStore")
        return self.session_store


def _params_dict(params: Any) -> dict[str, Any] | None:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    return None  # JSON-RPC list params are not used by MCP today.


# Action map convenience: pass directly to ``as_view`` so the URL conf
# stays compact.
ASYNC_STREAMABLE_HTTP_ACTION_MAP: dict[str, str] = {
    "post": "handle_jsonrpc",
    "get": "handle_get",
    "delete": "terminate_session",
}


__all__ = ["ASYNC_STREAMABLE_HTTP_ACTION_MAP", "AsyncStreamableHttpViewSet"]
