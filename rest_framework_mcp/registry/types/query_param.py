"""``QueryParam`` — re-exported from the sister repo, which owns the type.

The declaration is identical whichever transport carries it, so
``djangorestframework-services`` holds the single definition and every consumer
imports it. ``from rest_framework_mcp import QueryParam`` and
``from rest_framework_services import QueryParam`` are the same class.
"""

from __future__ import annotations

from rest_framework_services.types.query_param import QueryParam

__all__ = ["QueryParam"]
