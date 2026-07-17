"""MCP wiring for the job-status example."""

from __future__ import annotations

import django_filters
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from jobs.models import Job
from jobs.selectors import get_job, list_jobs
from jobs.serializers import JobOutputSerializer, StartJobInputSerializer
from jobs.services import start_job
from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


class JobFilterSet(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Job.STATUS_CHOICES)

    class Meta:
        model = Job
        fields = ["status"]


def build_server() -> MCPServer:
    server = MCPServer(
        name="job-status-example",
        version="0.0.1",
        # Dev-only: accepts any caller. Swap for the default
        # DjangoOAuthToolkitBackend (or your own) in production.
        auth_backend=AllowAnyBackend(),
        # Fine for single-process dev. The default DjangoCacheSessionStore
        # works across workers.
        session_store=InMemorySessionStore(),
    )

    server.register_service_tool(
        name="jobs.start",
        spec=ServiceSpec(
            service=start_job,
            input_serializer=StartJobInputSerializer,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=JobOutputSerializer,
            ),
            # The thread-dispatch happens *after* the HTTP response is
            # written, so a transaction here would commit too early. The
            # example uses ``atomic=False`` for that reason; production
            # workers usually publish to a queue, where this concern
            # disappears.
            atomic=False,
        ),
        description="Start a job and return immediately with its id.",
    )

    server.register_resource(
        name="job",
        uri_template="jobs://{job_id}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_job,
            output_serializer=JobOutputSerializer,
        ),
        description="Read the latest status of a single job.",
    )

    server.register_selector_tool(
        name="jobs.list",
        spec=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_jobs,
            output_serializer=JobOutputSerializer,
            filter_set=JobFilterSet,
        ),
        description="List jobs, optionally filtered by status, paginated.",
        ordering_fields=["created_at"],
        paginate=True,
    )

    return server
