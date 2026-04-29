from __future__ import annotations

from django.db import models


class Invoice(models.Model):
    """A simple invoice. The example exposes this model end-to-end over MCP."""

    number = models.CharField(max_length=32, unique=True)
    amount_cents = models.PositiveIntegerField()
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.number} ({self.amount_cents}¢)"
