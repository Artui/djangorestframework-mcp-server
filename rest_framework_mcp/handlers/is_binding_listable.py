"""Shared per-binding visibility check for the four list handlers.

Centralised so the four list handlers agree on what "listable" means: a binding
is hidden when any of its ``permissions`` denies the caller, unless
``always_listed=True`` opts it back in.

A permission may declare an ``is_listable(token)`` method for a list-time
specific decision — useful when its ``has_permission(request, token)`` reads
``request.arguments`` and would otherwise deny unfairly against the empty
list-time arguments. Without one, ``has_permission`` runs against a data-less
synthesised request, the right semantic for binding-level permissions such as
``ScopeRequired``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.utils import permission_verdict


def is_binding_listable(binding: Any, http_request: HttpRequest, token: TokenInfo) -> bool:
    """Return ``True`` if the binding should appear in a list response.

    ``binding`` is duck-typed: all four binding dataclasses carry the same shape
    — a ``permissions`` tuple and an ``always_listed`` bool — without sharing a
    base class, and ``Any`` saves importing all four to spell a union.
    """
    if getattr(binding, "always_listed", False):
        return True
    for perm in binding.permissions:
        listable: Any = getattr(perm, "is_listable", None)
        if listable is not None:
            verdict = permission_verdict(
                perm,
                listable(token),
                method="is_listable",
                effect="every binding would be listed regardless of the caller.",
            )
            if not verdict:
                return False
            continue
        # List-time visibility equals call-time permission against a data-less
        # request: the pool seeds are still meaningful, only the caller-supplied
        # ``arguments`` payload is absent.
        allowed = permission_verdict(
            perm,
            perm.has_permission(http_request, token),
            method="has_permission",
            effect="every binding would be listed regardless of the caller.",
        )
        if not allowed:
            return False
    return True


__all__ = ["is_binding_listable"]
