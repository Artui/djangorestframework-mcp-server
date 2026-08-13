"""Durable handles for work that outlives the request that asked for it.

The ``io.modelcontextprotocol/tasks`` extension. A task-eligible tool answers
``tools/call`` with a handle instead of a result; the work happens in a queue worker,
and the client polls ``tasks/get`` until the status is terminal. The seam is
deliberately narrow —
[`TaskExecutor`][rest_framework_mcp.tasks.types.task_executor.TaskExecutor] has one
method taking one string — so Celery, RQ, Dramatiq or a thread pool all satisfy it and
none of them is imported here.

**``create_task`` and ``run_task`` are deliberately not re-exported here**, for
a structural reason: ``MCPCallContext`` declares a ``TaskStore``, so importing
any task *type* executes this module, and ``run_task`` imports the
``tools/call`` handler while ``create_task`` reaches ``transport`` — either
closes a cycle. Import them from their own modules. Neither is the normal entry
point: task creation happens inside ``tools/call``, and workers call
``MCPServer.run_task``, which is the object that holds the registries.
"""

from rest_framework_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.tasks.report_task_progress import report_task_progress
from rest_framework_mcp.tasks.transition_task import transition_task
from rest_framework_mcp.tasks.types.task_executor import TaskExecutor
from rest_framework_mcp.tasks.types.task_record import TaskRecord
from rest_framework_mcp.tasks.types.task_store import TaskStore

__all__ = [
    "DjangoCacheTaskStore",
    "InMemoryTaskStore",
    "TaskExecutor",
    "TaskRecord",
    "TaskStore",
    "report_task_progress",
    "transition_task",
]
