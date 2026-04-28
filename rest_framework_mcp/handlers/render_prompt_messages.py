from __future__ import annotations

from typing import Any

from rest_framework_mcp.protocol.prompt_message import PromptMessage


def normalize_render_result(result: Any) -> list[PromptMessage]:
    """Coerce whatever the render callable produced into a list of messages.

    Accepted shapes (most-permissive at the bottom of the chain):

    - ``list[PromptMessage]`` — passed through.
    - ``list[dict]`` — each dict is treated as a wire-shaped ``PromptMessage``
      (must already have ``role`` / ``content``).
    - ``list[str]`` — each string becomes a user text message.
    - ``str`` — single user text message.
    - ``PromptMessage`` — wrapped in a list.

    Anything else raises ``TypeError`` so misbehaving render callables fail
    loudly instead of producing invalid wire payloads.
    """
    if isinstance(result, PromptMessage):
        return [result]
    if isinstance(result, str):
        return [PromptMessage.text(role="user", text=result)]
    if isinstance(result, list):
        out: list[PromptMessage] = []
        for item in result:
            if isinstance(item, PromptMessage):
                out.append(item)
            elif isinstance(item, str):
                out.append(PromptMessage.text(role="user", text=item))
            elif isinstance(item, dict) and "role" in item and "content" in item:
                out.append(PromptMessage(role=item["role"], content=item["content"]))
            else:
                raise TypeError(f"Unsupported prompt message shape: {item!r}")
        return out
    raise TypeError(f"Prompt render returned an unsupported value: {result!r}")


__all__ = ["normalize_render_result"]
