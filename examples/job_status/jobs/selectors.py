from __future__ import annotations

from django.db.models import QuerySet
from rest_framework_services.exceptions.service_error import ServiceError

from jobs.models import Job


def list_jobs() -> QuerySet[Job]:
    return Job.objects.all()


def get_job(*, job_id: str) -> Job:
    try:
        return Job.objects.get(pk=job_id)
    except Job.DoesNotExist as exc:
        raise ServiceError(f"Job {job_id} not found") from exc
