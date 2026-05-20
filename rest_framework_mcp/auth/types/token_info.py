from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenInfo:
    """Authenticated principal carried alongside an MCP request.

    Backends construct this once per request and attach it to the Django
    ``HttpRequest`` (as ``request.mcp_token``). Permission classes consult it
    to gate tool/resource access.

    - ``user``: the resolved Django user (or ``AnonymousUser``-equivalent).
      ``Any`` here because the user model is project-defined.
    - ``scopes``: OAuth scopes proven by the bearer token.
    - ``audience``: the ``aud`` claim — must match the canonical ``/mcp`` URL
      per RFC 8707; backends are responsible for that comparison.
    - ``raw``: backend-specific opaque payload (the ``AccessToken`` row, the
      JWT claims dict, etc.) for advanced use cases.
    """

    user: Any
    scopes: tuple[str, ...] = field(default_factory=tuple)
    audience: str | None = None
    raw: Any = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def has_all_scopes(self, scopes: list[str] | tuple[str, ...]) -> bool:
        return all(s in self.scopes for s in scopes)


__all__ = ["TokenInfo"]
