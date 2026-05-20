"""Shared per-binding visibility check for the four list handlers.

Centralised so ``tools/list`` / ``resources/list`` /
``resources/templates/list`` / ``prompts/list`` agree on what
"listable" means: a binding is hidden when every one of its
``permissions`` denies the caller, unless ``always_listed=True``
explicitly opts it back in.

Permissions can opt into a list-time-specific visibility decision by
declaring an ``is_listable(token)`` method — useful for permissions
whose ``has_permission(request, token)`` reads ``request.arguments``
and would otherwise deny against the empty list-time arguments
unfairly. The default (no ``is_listable`` method) falls back to
``has_permission`` with a data-less synthesised request, which is the
right semantic for binding-level permissions like ``ScopeRequired``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from rest_framework_mcp.auth.types.token_info import TokenInfo


def is_binding_listable(binding: Any, http_request: HttpRequest, token: TokenInfo) -> bool:
    """Return ``True`` if the binding should appear in a list response.

    ``binding`` is duck-typed because all four binding dataclasses
    (``ToolBinding``, ``SelectorToolBinding``, ``ResourceBinding``,
    ``PromptBinding``) carry the same shape — a ``permissions`` tuple
    and an ``always_listed`` bool — without sharing a base class.
    Typing as ``Any`` keeps the helper from importing all four
    dataclasses just to spell a union.
    """
    if getattr(binding, "always_listed", False):
        return True
    for perm in binding.permissions:
        listable: Any = getattr(perm, "is_listable", None)
        if listable is not None:
            if not listable(token):
                return False
            continue
        # Default: list-time visibility equals call-time permission,
        # evaluated against a data-less request. Pool seeds (request,
        # user, data) are still meaningful — only the caller-supplied
        # ``arguments`` payload is absent.
        if not perm.has_permission(http_request, token):
            return False
    return True


__all__ = ["is_binding_listable"]
