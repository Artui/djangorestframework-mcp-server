"""Shared checks for the three ``tasks/*`` handlers."""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.auth.principal_for_token import principal_for_token
from rest_framework_mcp.constants import TASKS_EXTENSION_ID, JsonRpcErrorCode
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.tasks.types.task_record import TaskRecord


def declares_tasks_extension(context: MCPCallContext) -> bool:
    """Whether *this request* declared the tasks extension.

    Per request, never remembered — the spec says a server must not answer with
    a task to a client that did not declare on that request, *"regardless of
    prior declarations"*, and the stateless revision leaves nowhere to remember
    one anyway.

    A legacy-era request declares nothing (its capabilities arrived once, at
    ``initialize``, and this field is empty for it), so legacy clients never
    reach the task path. That falls out of the shape rather than being an era
    branch, which is why there isn't one.
    """
    extensions: Any = context.client_capabilities.get("extensions")
    return isinstance(extensions, dict) and TASKS_EXTENSION_ID in extensions


def missing_capability_error() -> JsonRpcError:
    """The ``-32021`` a non-declaring client gets from anything task-shaped.

    ⚠ **Deliberately not the code the extension document prints.** That text
    says ``-32003`` while annotating it ``MISSING_REQUIRED_CLIENT_CAPABILITY``
    — the same constant the ratified core schema allocates as **-32021**. The
    extension is carrying a number from the era when tasks were part of the
    core protocol, before the error range was repartitioned; ``-32003`` now
    sits inside ``-32000..-32019``, which the core spec reserves for
    implementations and says *"the specification will never define codes in
    this sub-range"*. Following the extension literally would mean emitting a
    code that, by the core spec's own rule, means nothing across
    implementations.

    It would also be actively wrong *here*: ``-32003`` is one of the two codes
    this package burned when its error numbering was corrected, and a client
    from an older release still reads it as "not found".

    ``data.requiredCapabilities`` follows the extension's example shape, which
    is what a client actually reads to learn what to declare.
    """
    return JsonRpcError(
        JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY,
        "Missing required client capability",
        data={"requiredCapabilities": {"extensions": {TASKS_EXTENSION_ID: {}}}},
    )


def unknown_task_error(task_id: Any) -> JsonRpcError:
    """The one answer for "no such task", whatever the real reason.

    Never existed, expired, or belongs to somebody else — all ``-32602``, all
    with the same message. The uniformity is the security property: with no
    ``tasks/list`` and no session to scope by, an id's unguessability is the
    containment boundary, and an error that distinguished "not yours" from "not
    found" would turn any endpoint into an oracle confirming which ids are
    real.
    """
    return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown task: {task_id!r}")


def resolve_task_id(params: dict[str, Any] | None) -> str | JsonRpcError:
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "params must be an object")
    task_id: Any = params.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'taskId' is required and must be a string"
        )
    return task_id


def owned_by_caller(record: TaskRecord, context: MCPCallContext) -> bool:
    """Whether the calling principal is the one the task was created for.

    The same comparison session ownership makes, against the same derived id.
    It is a second layer rather than the only one — see
    :func:`unknown_task_error` — but it is the layer that matters when an id
    leaks through a log, a proxy or a shared client.
    """
    return record.principal_id == principal_for_token(context.token)


__all__ = [
    "declares_tasks_extension",
    "missing_capability_error",
    "owned_by_caller",
    "resolve_task_id",
    "unknown_task_error",
]
