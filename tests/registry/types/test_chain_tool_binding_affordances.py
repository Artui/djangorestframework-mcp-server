"""A chain refuses, at registration, a rendered step whose affordances it cannot answer.

drf-services renders a selector spec's ``affordances`` from answers its selector
dispatch computes. A chain runs each step's selector directly, so none are
computed, and rendering raised ``ImproperlyConfigured`` on every call -- while the
tool registered cleanly and ``tools/list`` advertised the ``affordances`` object.

The refusal is held to exactly the shapes that failed. The half that stays
registrable matters as much as the half that is refused, so each accepted shape is
also called, to show it is accepted because it works rather than by oversight.

Registration goes through ``MCPServer.register_chain_tool``, the public surface, so
these run unchanged against a tree without the refusal.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep
from tests.testapp.affordances import fresh_server, order_selector_spec


def _service_rendering(kind: SelectorKind, **overrides: Any) -> ServiceSpec[Any, Any, Any]:
    """A service whose ``output_selector_spec`` renders the order and declares ``cancel``."""
    return ServiceSpec(
        service=lambda **_: None,
        atomic=False,
        output_selector_spec=order_selector_spec(kind, **overrides),
    )


def _register(steps: list[ChainStep], **kwargs: Any) -> Any:
    server = fresh_server()
    server.register_chain_tool(name="orders", steps=steps, atomic=False, permissions=[], **kwargs)
    return server


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(
            ChainStep("out", order_selector_spec(SelectorKind.RETRIEVE)), id="selector-retrieve"
        ),
        pytest.param(ChainStep("out", order_selector_spec(SelectorKind.LIST)), id="selector-list"),
        pytest.param(
            ChainStep("out", _service_rendering(SelectorKind.RETRIEVE)), id="service-retrieve"
        ),
        pytest.param(ChainStep("out", _service_rendering(SelectorKind.LIST)), id="service-list"),
    ],
)
def test_an_output_step_declaring_affordances_is_refused(step: ChainStep) -> None:
    with pytest.raises(ImproperlyConfigured) as raised:
        _register([step])

    message = str(raised.value)
    assert "Chain tool 'orders': step 'out'" in message
    assert "['cancel']" in message
    assert "selector or service tool of its own" in message
    assert "drop the affordances from this step" in message


def test_output_all_refuses_a_step_that_is_not_the_output_step() -> None:
    """Under ``output_all`` every step with a serializer is rendered, so the first
    one fails as surely as the last -- and the message names the one that does."""
    with pytest.raises(ImproperlyConfigured, match="step 'orders_step'"):
        _register(
            [
                ChainStep("orders_step", order_selector_spec(SelectorKind.LIST)),
                ChainStep("last", order_selector_spec(SelectorKind.RETRIEVE, affordances=None)),
            ],
            output_all=True,
        )


async def test_an_intermediate_step_declaring_affordances_is_accepted_and_runs() -> None:
    """Never rendered, so its declaration reads no answer: its result only feeds the
    next step, and the call succeeds."""
    server = _register(
        [
            ChainStep("lookup", order_selector_spec(SelectorKind.RETRIEVE)),
            ChainStep("out", order_selector_spec(SelectorKind.RETRIEVE, affordances=None)),
        ]
    )

    out: Any = await server.acall_tool("orders", user=None)

    assert out["structuredContent"] == {"number": "A-1"}


async def test_an_unrendered_output_step_is_accepted_and_runs() -> None:
    """No output serializer, so the value passes through unrendered."""
    server = _register(
        [ChainStep("out", order_selector_spec(SelectorKind.RETRIEVE, output_serializer=None))]
    )

    out: Any = await server.acall_tool("orders", user=None)

    assert out["structuredContent"] == {"number": "A-1"}


async def test_a_declaration_asking_nothing_is_accepted_and_renders() -> None:
    """A name whose service declares no conditions is answered without reading a
    row, so it renders -- and it is what keeps a chain's advertised ``affordances``
    object reachable at all."""
    server = _register(
        [
            ChainStep(
                "out",
                order_selector_spec(
                    SelectorKind.RETRIEVE,
                    affordances={"archive": ServiceSpec(service=lambda **_: None)},
                ),
            )
        ]
    )

    out: Any = await server.acall_tool("orders", user=None)

    assert out["structuredContent"] == {
        "number": "A-1",
        "affordances": {"archive": {"available": True}},
    }
