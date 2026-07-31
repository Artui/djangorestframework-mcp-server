from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode, ResultType, TaskPolicy
from rest_framework_mcp.handlers.tasks_utils import (
    declares_tasks_extension,
    missing_capability_error,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.task import Task
from rest_framework_mcp.tasks.create_task import create_task


def maybe_create_task(
    binding: Any,
    arguments: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError | None:
    """Answer a tool call with a task handle, if this call should get one.

    Returns ``None`` when it should not — which is the overwhelmingly common
    case — and the caller runs the tool inline exactly as before. Anything else
    is the response.

    **Three conditions, and all three must hold.** The binding must opt in
    (:class:`TaskPolicy`), the server must actually be able to run tasks, and
    the client must have declared the extension **on this request**. Each
    failure has a different right answer, which is what most of this function
    is:

    - *Binding says no* → run inline. Nothing to explain.
    - *Server cannot run tasks* → run inline if the policy allows it. If the
      policy is ``REQUIRED``, this is a **misconfiguration on our side**, not a
      client error: a tool was declared un-runnable inline and nothing was
      given to run it elsewhere. It gets ``-32603``, because blaming the
      client's request for a missing executor would send a competent client
      round a loop it cannot win.
    - *Client did not declare* → run inline if the policy allows it; otherwise
      ``-32021``, which is exactly the case the spec describes as a server
      *"unable to service a request ... without returning CreateTaskResult"*.

    ⚠ **Permissions are checked here, before anything is created.** A task is
    durable, queued work with the caller's authorization context stored
    alongside it; creating one for a caller who may not run the tool would put
    a denied call in the queue and let its own permission check fail later, in
    a worker, where the ``403`` has nowhere to go. Rate limits go through the
    same reasoning as permissions but are *consumed* rather than tested, so
    they are charged here and deliberately not charged again in the worker —
    see ``enforce_rate_limits`` on :class:`MCPCallContext`.
    """
    policy: TaskPolicy = getattr(binding, "task_policy", TaskPolicy.FORBIDDEN)
    if policy is TaskPolicy.FORBIDDEN:
        return None

    store = context.tasks
    executor = context.task_executor
    if store is None or executor is None:
        if policy is TaskPolicy.REQUIRED:
            return JsonRpcError(
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"Tool {binding.name!r} is registered to run only as a task, but this "
                "server has no task store and executor configured. Pass task_store= "
                "and task_executor= to MCPServer(...), or relax the tool's task_policy.",
            )
        return None

    if not declares_tasks_extension(context):
        if policy is TaskPolicy.REQUIRED:
            return missing_capability_error()
        return None

    allowed, required_scopes = check_permissions(
        binding.permissions, context.http_request, context.token
    )
    if not allowed:
        return JsonRpcError(
            JsonRpcErrorCode.FORBIDDEN,
            "Insufficient permission",
            data={"requiredScopes": required_scopes} if required_scopes else None,
        )

    task: Task = create_task(
        store=store,
        executor=executor,
        tool_name=binding.name,
        arguments=arguments,
        token=context.token,
        ttl_ms=context.config.task_ttl_ms,
        poll_interval_ms=context.config.task_poll_interval_ms,
    )
    # ⚠ ``resultType`` is set explicitly and only here. The response envelope
    # stamps ``complete`` on anything that has not already declared itself, and
    # ``task`` is the one value a handler must claim for itself — the spec
    # forbids it on any result other than this one.
    return {"resultType": ResultType.TASK.value, **task.to_dict()}


__all__ = ["maybe_create_task"]
