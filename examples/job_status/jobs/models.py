from __future__ import annotations

import uuid

from django.db import models


class Job(models.Model):
    """A long-running job. Status transitions: ``queued`` → ``running`` → ``done`` (or ``failed``)."""

    STATUS_CHOICES: list[tuple[str, str]] = [
        ("queued", "queued"),
        ("running", "running"),
        ("done", "done"),
        ("failed", "failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    duration_seconds = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Job {self.id} ({self.status})"
