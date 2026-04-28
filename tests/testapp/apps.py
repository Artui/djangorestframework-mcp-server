from __future__ import annotations

from django.apps import AppConfig


class TestappConfig(AppConfig):
    name = "tests.testapp"
    label = "testapp"
    default_auto_field = "django.db.models.AutoField"
