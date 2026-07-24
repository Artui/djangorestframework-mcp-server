from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UrlKwarg:
    """A URL-derived value a tool advertises and seeds into the off-HTTP view ``kwargs``.

    The MCP counterpart of a nested route's URL captures (the ``project_pk`` of
    ``/projects/{project_pk}/widgets/``). Over HTTP such a value comes from the
    route and reaches a spec through ``view.kwargs`` — directly, or through a
    ``spec.kwargs`` provider that scopes by it (a tenant/role lookup). Over MCP
    there is no route, so the model supplies it as a tool argument: the tool
    advertises it in its ``inputSchema``, the dispatcher pops it out of the
    arguments and seeds it into ``build_offline_context(kwargs=…)`` /
    ``OfflineServiceView.kwargs``, and drf-services spreads it into the selector /
    target pools (authoritative over the spec params, below a ``spec.kwargs``
    provider). It never reaches the spec as an ordinary input, so the
    unknown-argument policy never flags it.

    Reach for a ``UrlKwarg`` when the value is a URL-derived input a spec depends
    on that is **not** an ordinary tool argument — most commonly a scoping
    ``spec.kwargs`` provider that reads ``view.kwargs`` (over MCP that mapping is
    otherwise empty, so the provider mis-scopes for every caller).

    - ``name`` — the tool-argument / view-kwarg key. Must not collide with a
      reserved transport key (``ordering`` / ``page`` / ``limit`` pagination knobs,
      or the ``request`` / ``user`` / ``data`` / ``instance`` / ``serializer`` pool
      seeds).
    - ``type`` — the JSON-Schema type advertised to the model (``"string"`` by
      default; ``"integer"`` / ``"number"`` / ``"boolean"`` …).
    - ``description`` — optional help text shown to the model.
    - ``default`` — optional value seeded when the model omits the argument; also
      surfaced as the schema ``default``.
    """

    name: str
    type: str = "string"
    description: str | None = None
    default: Any = None

    def json_schema(self) -> dict[str, Any]:
        """The JSON-Schema property this kwarg contributes to a tool's ``inputSchema``."""
        schema: dict[str, Any] = {"type": self.type}
        if self.description is not None:
            schema["description"] = self.description
        if self.default is not None:
            schema["default"] = self.default
        return schema


__all__ = ["UrlKwarg"]
