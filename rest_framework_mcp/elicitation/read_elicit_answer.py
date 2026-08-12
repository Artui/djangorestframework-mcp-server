from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import ELICITATION_KEY, ElicitAction
from rest_framework_mcp.elicitation.types.elicit_answer import ElicitAnswer


def read_elicit_answer(params: dict[str, Any]) -> ElicitAnswer | None:
    """Pull this package's answer out of a retry's ``inputResponses``.

    ``None`` for every shape that is not an answer we can use — key absent, map
    malformed, an ``action`` the spec does not define — which is deliberately
    the same outcome as "the client did not answer". The spec asks for exactly
    that: where information is missing the server *"SHOULD respond with a new
    ``InputRequiredResult`` requesting the missing information again, rather
    than returning an error"*, and falling through to the service produces that
    new result without this layer having to know.

    Other keys in the map are ignored rather than rejected — also the spec's
    instruction (*"SHOULD ignore any information it does not recognize or
    need"*), which keeps a client free to batch answers for requests this server
    never sent.
    """
    responses: Any = params.get("inputResponses")
    if not isinstance(responses, dict):
        return None
    answer: Any = responses.get(ELICITATION_KEY)
    if not isinstance(answer, dict):
        return None
    try:
        action = ElicitAction(answer.get("action"))
    except ValueError:
        return None
    content: Any = answer.get("content")
    return ElicitAnswer(action=action, content=content if isinstance(content, dict) else {})


__all__ = ["read_elicit_answer"]
