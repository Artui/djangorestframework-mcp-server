"""``QueryParam`` — re-exported from the sister repo, which owns the type.

The sibling of :mod:`rest_framework_mcp.registry.types.url_kwarg`, and re-exported
for the same reason: the declaration is identical whichever transport carries it,
so ``djangorestframework-services`` owns the single definition and both this
package and ``djangorestframework-pydantic-ai`` consume it. Declaring a local copy
is what let ``UrlKwarg`` drift between the two packages before 0.28.

``from rest_framework_mcp import QueryParam`` and
``from rest_framework_services import QueryParam`` are the same class.
"""

from __future__ import annotations

from rest_framework_services.types.query_param import QueryParam

__all__ = ["QueryParam"]
