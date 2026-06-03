from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.chain_context import ChainContext


@dataclass(frozen=True)
class ChainStep:
    """One step in a :class:`~rest_framework_mcp.registry.types.chain_tool_binding.ChainToolBinding`.

    A step wraps a single ``ServiceSpec`` (a write) or ``SelectorSpec`` (a
    read) and binds its output to ``alias`` so later steps can read it via
    ``ctx[alias]``.

    Fields:

    - ``alias`` — the name this step's result is stored under in the
      :class:`ChainContext`. Must be unique within the chain.
    - ``spec`` — the ``ServiceSpec`` or ``SelectorSpec`` this step runs.
    - ``inputs`` — optional ``(ctx) -> Mapping`` callable returning the kwargs
      merged into the step's call pool (alongside ``request`` / ``user``);
      ``resolve_callable_kwargs`` then filters them to the callable's
      signature. ``None`` (the default) means the step receives only
      ``request`` / ``user`` / ``data`` (the validated chain ``args``) — handy
      for a first step whose service takes the chain input as ``data``. Any
      step that needs a prior output or a reshaped payload supplies
      ``inputs`` explicitly.

    The step's result stored under ``alias`` is the *final* value — for a
    ``ServiceSpec`` with an ``output_selector_spec.selector`` that means the
    re-fetched value, so a downstream step reads what the response would
    serialize.
    """

    alias: str
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]
    inputs: Callable[[ChainContext], Mapping[str, Any]] | None = None


__all__ = ["ChainStep"]
