from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChainContext:
    """The accumulating context a chain tool threads through its steps.

    Passed to each :class:`~rest_framework_mcp.registry.types.chain_step.ChainStep`'s
    ``inputs`` callable so a step can build its call kwargs from the
    validated tool arguments and any prior step's output:

    .. code-block:: python

        inputs=lambda ctx: {"account_id": ctx["acct"].id, **ctx.args}

    - ``args`` — the validated chain input (a dataclass instance, a dict, or
      the raw arguments mapping when no input serializer is resolved).
    - ``ctx[alias]`` — the (post-output-selector) result a prior step stored
      under its alias. ``KeyError`` if the alias hasn't run yet, which can
      only happen if a step references a later alias — a wiring bug worth
      surfacing loudly.
    - ``request`` / ``user`` — the synthesised DRF request and the
      authenticated user, for steps whose ``inputs`` need them.

    Mutable by design: the dispatcher appends to ``outputs`` as each step
    completes. A fresh instance is built per tool call, so there is no
    cross-request shared state.
    """

    args: Any
    request: Any
    user: Any
    outputs: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, alias: str) -> Any:
        return self.outputs[alias]

    def __contains__(self, alias: object) -> bool:
        return alias in self.outputs


__all__ = ["ChainContext"]
