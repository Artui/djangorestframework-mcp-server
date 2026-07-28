"""``UrlKwarg`` — re-exported from the sister repo, which now owns the type.

The declaration is identical whichever transport carries it, and this package's
copy had drifted from ``djangorestframework-pydantic-ai``'s: the two validated
the same declaration against different reserved-name sets, so ``UrlKwarg("order")``
was legal here and rejected there, and ``UrlKwarg("user")`` the reverse.
``djangorestframework-services`` 0.28 owns the single definition, which also
carries the new ``required`` flag.

The import path here is preserved permanently — ``from rest_framework_mcp import
UrlKwarg`` keeps working, so consumers need only a version bump. New code may
import from either location; they are the same class.
"""

from __future__ import annotations

from rest_framework_services.types.url_kwarg import UrlKwarg

__all__ = ["UrlKwarg"]
