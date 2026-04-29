"""MCP wiring for the job-status example."""

from __future__ import annotations

import django_filters
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from jobs.models import Job
from jobs.selectors import get_job, list_jobs
from jobs.serializers import JobOutputSerializer, StartJobInputSerializer
from jobs.services import start_job
from rest_framework_mcp import MCPServer


class JobFilterSet(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Job.STATUS_CHOICES)

    class Meta:
        model = Job
        fields = ["status"]


def build_server() -> MCPServer:
    server = MCPServer(name="job-status")

    server.register_service_tool(
        name="jobs.start",
        spec=ServiceSpec(
            service=start_job,
            input_serializer=StartJobInputSerializer,
            output_serializer=JobOutputSerializer,
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
            selector=get_job,
            output_serializer=JobOutputSerializer,
        ),
        description="Read the latest status of a single job.",
    )

    server.register_selector_tool(
        name="jobs.list",
        spec=SelectorSpec(
            selector=list_jobs,
            output_serializer=JobOutputSerializer,
        ),
        description="List jobs, optionally filtered by status, paginated.",
        filter_set=JobFilterSet,
        ordering_fields=["created_at"],
        paginate=True,
    )

    return server
