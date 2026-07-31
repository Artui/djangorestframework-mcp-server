"""The two moments a ``tools/call`` touches the elicitation exchange.

Both sides of one round trip: :func:`resolve_prior_input` before the service
runs, :func:`ask_for_input` if it raises. Everything protocol-shaped is in
:mod:`rest_framework_mcp.elicitation`; what lives here is the composition —
which is tool-shaped, because ``isError`` is the failure channel and only a tool
result has one.

⛔ **Service tools only.** Chain and selector tools deliberately do not reach
this module, and ``AdditionalInputRequired`` from either degrades through the
existing ``ServiceError`` arm to an ordinary ``isError`` result.

- A **chain** would be the dangerous one. MRTR completes a call by *re-running
  it from the top*; a chain that asked at step three would, on the retry, run
  steps one and two a second time. There is no shape of this that is safe
  without checkpointing the chain, which is the tasks extension's problem, not
  this one.
- A **selector** is a read. A read that needs the user to decide something is a
  tool wearing the wrong registration.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.exceptions.additional_input_required import AdditionalInputRequired

from rest_framework_mcp.auth.principal_for_token import principal_for_token
from rest_framework_mcp.constants import ELICITATION_KEY, ElicitAction, JsonRpcErrorCode
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
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError

TOOLS_CALL_METHOD: str = "tools/call"


def resolve_prior_input(
    params: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    context: MCPCallContext,
) -> ResolvedInput:
    """Fold any earlier round's answers into the arguments this call runs with.

    Cheap and unconditional: an ordinary first call carries neither
    ``inputResponses`` nor ``requestState``, so this returns the arguments it
    was given with a fingerprint attached and nothing else happens.

    The fingerprint is computed from the arguments **as sent**, before the
    merge, which is what lets the next round's state bind to a call the client
    will present again unchanged.
    """
    fingerprint: str = fingerprint_request(
        TOOLS_CALL_METHOD, {"name": tool_name, "arguments": arguments}
    )
    state: RequestState | None = verify_request_state(
        params.get("requestState"),
        principal=principal_for_token(context.token),
        fingerprint=fingerprint,
        max_age=context.config.input_request_ttl_seconds,
    )
    answer: ElicitAnswer | None = read_elicit_answer(params)

    if answer is not None and answer.action is not ElicitAction.ACCEPT:
        return ResolvedInput(
            arguments=arguments,
            fingerprint=fingerprint,
            round=state.round if state else 0,
            refused_with=answer.action,
        )

    # ⚠ Answers this round are merged over the carried ones, and the carried
    # ones over the client's arguments — so the most recently confirmed value
    # wins at every layer. Nothing here trusts ``carried`` on its own: it only
    # exists because it arrived inside a signature bound to this principal and
    # this call.
    carried: dict[str, Any] = dict(state.answers) if state else {}
    if answer is not None:
        carried.update(answer.content)
    return ResolvedInput(
        arguments={**arguments, **carried},
        fingerprint=fingerprint,
        round=state.round if state else 0,
        carried=carried,
    )


def ask_for_input(
    exc: AdditionalInputRequired,
    prior: ResolvedInput,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Turn a service's *"I need something else"* into whatever this client can use.

    Three outcomes, and the ordering between them is deliberate:

    1. **A malformed schema is a ``-32603``**, checked before anything else. It
       is a bug in the service, it is a bug for every caller, and finding out
       only when a capable client happens to call is how it survives to
       production.
    2. **An ``InputRequiredResult``** when this client declared elicitation, the
       service said what it wants, and the round budget is not spent.
    3. **An ordinary ``isError`` result** otherwise, carrying the service's
       message and the shape of what is missing.

    ⭐ (3) is the interesting one, and it is why this does *not* answer a
    non-declaring client with ``-32021 MissingRequiredClientCapability``. That
    code is for a capability the server **requires**; this server merely
    *prefers* one. A legacy client, a client that only does URL-mode
    elicitation, and a task worker replaying a call with nobody to ask are all
    served better by being told what is missing than by a protocol error — a
    model reading ``{"error": {"requestedInput": {"confirmed": ...}}}`` can
    supply the argument itself on the next call, which is the whole point of the
    exchange and does not need a dialog to reach.
    """
    if not exc.schema:
        # A message with no schema is a service saying it needs *something*
        # without saying what. There is no form to render, so the message alone
        # is the entire answer.
        return _degraded(exc)

    try:
        requested_schema: dict[str, Any] = build_requested_schema(exc.schema)
    except ImproperlyConfigured as bad_schema:
        return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(bad_schema))

    if not can_ask_client(context.client_capabilities):
        return _degraded(exc)
    if prior.round >= context.config.max_input_rounds:
        return _degraded(exc)

    state = RequestState(
        principal=principal_for_token(context.token),
        fingerprint=prior.fingerprint,
        answers=prior.carried,
        round=prior.round + 1,
    )
    return InputRequiredResult(
        input_requests={ELICITATION_KEY: ElicitRequest(exc.message, requested_schema)},
        request_state=sign_request_state(state),
    ).to_dict()


def refusal_result(action: ElicitAction) -> dict[str, Any]:
    """The tool result for a user who was asked and did not answer.

    Kept apart from the failure above because the two say different things to a
    model deciding what to do next: an unanswerable question is worth working
    around, and a declined one is not worth asking twice.
    """
    declined: bool = action is ElicitAction.DECLINE
    message: str = (
        "The user declined to provide the input this tool asked for."
        if declined
        else "The user dismissed the request for input without answering."
    )
    return build_error_tool_result(
        message,
        error_type="input_declined" if declined else "input_cancelled",
        detail={"action": action.value},
    ).to_dict()


def _degraded(exc: AdditionalInputRequired) -> dict[str, Any]:
    """The service's own message, plus the shape of what it wanted, as an error."""
    return build_error_tool_result(
        exc.message,
        error_type="input_required",
        detail={"requestedInput": dict(exc.schema)} if exc.schema else None,
    ).to_dict()


__all__ = ["TOOLS_CALL_METHOD", "ask_for_input", "refusal_result", "resolve_prior_input"]
