"""``UrlKwarg`` — re-exported from the sister repo, which owns the type.

The declaration is identical whichever transport carries it, so
``djangorestframework-services`` holds the single definition and every consumer
imports it — a local copy is what let the reserved-name sets drift apart
between packages. This import path is preserved permanently:
``from rest_framework_mcp import UrlKwarg`` and
``from rest_framework_services import UrlKwarg`` are the same class.
"""

from __future__ import annotations

from rest_framework_services.types.url_kwarg import UrlKwarg

__all__ = ["UrlKwarg"]
