from __future__ import annotations

from django.db import models


class Invoice(models.Model):
    number = models.CharField(max_length=32)
    amount_cents = models.IntegerField(default=0)
    sent = models.BooleanField(default=False)

    class Meta:
        app_label = "testapp"

    def __str__(self) -> str:
        return self.number
