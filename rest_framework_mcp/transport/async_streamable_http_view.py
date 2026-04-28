from __future__ import annotations

import json
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.auth.auth_backend import MCPAuthBackend
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.handlers.async_dispatch import adispatch
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.protocol.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.protocol.parse_message import parse_message
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.transport.origin_validation import is_origin_allowed
from rest_framework_mcp.transport.protocol_version import resolve_protocol_version
from rest_framework_mcp.transport.session_store import SessionStore
from rest_framework_mcp.transport.sse_broker import SSEBroker
from rest_framework_mcp.transport.sse_replay_buffer import SSEReplayBuffer
from rest_framework_mcp.transport.sse_response import build_sse_response

_SESSION_HEADER: str = "Mcp-Session-Id"
_VERSION_HEADER: str = "Mcp-Protocol-Version"


def _error_response(
    *, code: int, message: str, data: Any = None, status: int = 400, request_id: Any = None
) -> JsonResponse:
    body: dict[str, Any] = JsonRpcResponse(
        id=request_id, error=JsonRpcError(code=code, message=message, data=data)
    ).to_dict()
    return JsonResponse(body, status=status)


@method_decorator(csrf_exempt, name="dispatch")
class AsyncStreamableHttpView(View):
    """Async sibling of :class:`StreamableHttpView` for ASGI deployments.

    Wire behaviour matches the sync view for POST and DELETE — same headers,
    same status codes, same JSON-RPC shapes. The async path additionally
    supports server-initiated SSE on GET when an :class:`SSEBroker` is wired
    in; without one, GET returns 405 (the spec explicitly allows this when
    the server has nothing to push).

    The async view dispatches I/O-bound handlers via :func:`arun_service` /
    :func:`arun_selector` and bridges sync collaborators (auth backend,
    session store, permissions) through :func:`asgiref.sync.sync_to_async`
    so a fully sync stack still works correctly under ASGI.

    Wire collaborators (registries, auth backend, session store, broker)
    are passed through ``as_view`` from :class:`MCPServer.async_urls`. There
    is no module-level lookup, matching the sync view's contract.
    """

    http_method_names = ["post", "get", "delete", "options"]

    tools: ToolRegistry | None = None
    resources: ResourceRegistry | None = None
    prompts: PromptRegistry | None = None
    auth_backend: MCPAuthBackend | None = None
    session_store: SessionStore | None = None
    sse_broker: SSEBroker | None = None
    sse_replay_buffer: SSEReplayBuffer | None = None

    async def post(self, request: HttpRequest) -> HttpResponse:
        guard: HttpResponse | None = self._check_origin(request)
        if guard is not None:
            return guard

        max_bytes: int = int(get_setting("MAX_REQUEST_BYTES"))
        if len(request.body) > max_bytes:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Request body too large",
                status=413,
            )

        try:
            payload: Any = json.loads(request.body or b"null")
        except json.JSONDecodeError as exc:
            return _error_response(
                code=JsonRpcErrorCode.PARSE_ERROR, message=f"Invalid JSON: {exc.msg}"
            )

        try:
            message = parse_message(payload)
        except ValueError as exc:
            return _error_response(code=JsonRpcErrorCode.INVALID_REQUEST, message=str(exc))

        is_initialize: bool = isinstance(message, JsonRpcRequest) and message.method == "initialize"

        version_header: str | None = request.headers.get(_VERSION_HEADER)
        if is_initialize:
            protocol_version: str = (
                resolve_protocol_version(version_header)
                or list(get_setting("PROTOCOL_VERSIONS"))[0]
            )
        else:
            resolved: str | None = resolve_protocol_version(version_header)
            if resolved is None:
                return _error_response(
                    code=JsonRpcErrorCode.INVALID_REQUEST,
                    message="Missing or unsupported MCP-Protocol-Version",
                    request_id=getattr(message, "id", None),
                )
            protocol_version = resolved

        store = self._require_session_store()
        session_id: str | None = request.headers.get(_SESSION_HEADER)
        if not is_initialize and (not session_id or not await acall(store.exists, session_id)):
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Unknown or missing MCP-Session-Id",
                status=404,
                request_id=getattr(message, "id", None),
            )

        backend = self._require_auth_backend()
        token = await acall(backend.authenticate, request)
        if token is None:
            challenge: str = backend.www_authenticate_challenge(error="invalid_token")
            response = JsonResponse(
                {"error": "unauthorized", "error_description": "Authentication required."},
                status=401,
            )
            response["WWW-Authenticate"] = challenge
            return response

        context = MCPCallContext(
            http_request=request,
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

    async def get(self, request: HttpRequest) -> HttpResponseBase:
        """Open a server-pushed SSE stream for the current MCP session.

        Spec compliance:
          - 405 if no broker is configured (server has nothing to push).
          - 400 if the protocol-version header is missing/unsupported (parity
            with POST — the spec is silent on GET version handling, but
            consistent enforcement avoids surprising behavior).
          - 404 if the supplied session id is unknown.
          - Otherwise: ``text/event-stream`` with idle keep-alives and any
            payloads ``MCPServer.notify`` enqueues.
        """
        guard: HttpResponse | None = self._check_origin(request)
        if guard is not None:
            return guard

        if self.sse_broker is None:
            return HttpResponse(status=405)

        version_header: str | None = request.headers.get(_VERSION_HEADER)
        if resolve_protocol_version(version_header) is None:
            return _error_response(
                code=JsonRpcErrorCode.INVALID_REQUEST,
                message="Missing or unsupported MCP-Protocol-Version",
            )

        session_id: str | None = request.headers.get(_SESSION_HEADER)
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
            request.headers.get("Last-Event-ID") if self.sse_replay_buffer is not None else None
        )
        return build_sse_response(
            self.sse_broker,
            session_id,
            replay_buffer=self.sse_replay_buffer,
            last_event_id=last_event_id,
        )

    async def delete(self, request: HttpRequest) -> HttpResponse:
        guard: HttpResponse | None = self._check_origin(request)
        if guard is not None:
            return guard
        session_id: str | None = request.headers.get(_SESSION_HEADER)
        if session_id:
            # Drop replay history first; the session is going away and any
            # buffered events would never be delivered.
            if self.sse_replay_buffer is not None:
                await self.sse_replay_buffer.forget(session_id)
            await acall(self._require_session_store().destroy, session_id)
        return HttpResponse(status=204)

    # ----- helpers -----

    def _check_origin(self, request: HttpRequest) -> HttpResponse | None:
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
            raise RuntimeError("AsyncStreamableHttpView is missing a ToolRegistry")
        return self.tools

    def _require_resources(self) -> ResourceRegistry:
        if self.resources is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a ResourceRegistry")
        return self.resources

    def _require_prompts(self) -> PromptRegistry:
        if self.prompts is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a PromptRegistry")
        return self.prompts

    def _require_auth_backend(self) -> MCPAuthBackend:
        if self.auth_backend is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing an MCPAuthBackend")
        return self.auth_backend

    def _require_session_store(self) -> SessionStore:
        if self.session_store is None:  # pragma: no cover - guarded by MCPServer
            raise RuntimeError("AsyncStreamableHttpView is missing a SessionStore")
        return self.session_store


def _params_dict(params: Any) -> dict[str, Any] | None:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    return None  # JSON-RPC list params are not used by MCP today.


__all__ = ["AsyncStreamableHttpView"]
