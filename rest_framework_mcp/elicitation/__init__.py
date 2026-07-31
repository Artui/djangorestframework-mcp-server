"""Asking the user a question, the way a stateless protocol has to.

From ``2026-07-28`` a server cannot send a request to a client: the direction
was removed and replaced with **multi round-trip requests**. A call that needs
something it was not given answers with an ``InputRequiredResult`` instead of a
result, the client collects the input, and it **retries the original call**
carrying the answers. Two independent requests, nothing held in between.

What the pieces here do, in the order a call meets them:

1. :func:`~rest_framework_mcp.elicitation.read_elicit_answer.read_elicit_answer`
   and :func:`~rest_framework_mcp.elicitation.verify_request_state.verify_request_state`
   recover what an earlier round established, if this is a retry.
2. The service runs, and may raise ``AdditionalInputRequired``.
3. :func:`~rest_framework_mcp.elicitation.can_ask_client.can_ask_client` decides
   whether this client can be asked at all, and
   :func:`~rest_framework_mcp.elicitation.build_requested_schema.build_requested_schema`
   turns the service's declaration into the form.
4. :func:`~rest_framework_mcp.elicitation.sign_request_state.sign_request_state`
   seals what the next round will need to trust.

⭐ **The service's involvement is one ``raise``.** It never holds a callback and
is never resumed — it re-runs from the top with the answer present as an
ordinary argument. That falls out of statelessness rather than being a choice,
and it is why the transport-neutral half of this lives in drf-services as a bare
exception carrying a message and a schema.
"""

from rest_framework_mcp.elicitation.build_requested_schema import build_requested_schema
from rest_framework_mcp.elicitation.can_ask_client import can_ask_client
from rest_framework_mcp.elicitation.fingerprint_request import fingerprint_request
from rest_framework_mcp.elicitation.read_elicit_answer import read_elicit_answer
from rest_framework_mcp.elicitation.sign_request_state import sign_request_state
from rest_framework_mcp.elicitation.types.elicit_answer import ElicitAnswer
from rest_framework_mcp.elicitation.types.elicit_request import ElicitRequest
from rest_framework_mcp.elicitation.types.input_required_result import InputRequiredResult
from rest_framework_mcp.elicitation.types.request_state import RequestState
from rest_framework_mcp.elicitation.types.resolved_input import ResolvedInput
from rest_framework_mcp.elicitation.verify_request_state import verify_request_state

__all__ = [
    "ElicitAnswer",
    "ElicitRequest",
    "InputRequiredResult",
    "RequestState",
    "ResolvedInput",
    "build_requested_schema",
    "can_ask_client",
    "fingerprint_request",
    "read_elicit_answer",
    "sign_request_state",
    "verify_request_state",
]
