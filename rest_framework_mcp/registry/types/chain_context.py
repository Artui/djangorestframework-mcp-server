from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChainContext:
    """The accumulating context a chain tool threads through its steps.

    Passed to each [`ChainStep`][rest_framework_mcp.registry.types.chain_step.ChainStep]'s
    ``inputs`` callable so a step can build its call kwargs from the validated
    tool arguments and any prior step's output:

    ```python
    inputs=lambda ctx: {"account_id": ctx["acct"].id, **ctx.args}
    ```

    ``ctx[alias]`` is the post-output-selector result a prior step stored, and
    raises ``KeyError`` for an alias that has not run — only possible when a
    step references a later one, a wiring bug worth surfacing loudly. Mutable
    by design, and built fresh per tool call, so there is no cross-request
    shared state.

    Attributes:
        args: The validated chain input — a dataclass instance, a dict, or the
            raw arguments mapping when no input serializer is resolved.
        request: The synthesised DRF request.
        user: The authenticated user.
        outputs: Alias to step result, filled in as the chain runs.
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
