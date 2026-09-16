"""``rendered_affordances`` reads what the renderer reads, on every binding kind.

drf-services dispatches on the spec's class to find the mapping it renders: a
``SelectorSpec``'s own, a ``ServiceSpec``'s ``output_selector_spec``. A
``ServiceSpec`` also has ``affordances`` of its own, the conditions a call is
refused against, which are never rendered -- so reading the attribute off
whichever spec is to hand would advertise the wrong declaration. These bindings
share no base class, so that they agree is a test rather than a signature.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from tests.testapp.affordances import CANCEL_ORDER, OrderSerializer, order_selector_spec


def _service(output_selector_spec: SelectorSpec[Any, Any] | None) -> ServiceSpec[Any, Any, Any]:
    # Its own affordances are declared throughout, so any binding reading them
    # instead of the rendered mapping answers something other than expected.
    return ServiceSpec(
        service=lambda **_: {"number": "A-1"},
        atomic=False,
        affordances=CANCEL_ORDER.affordances,
        output_selector_spec=output_selector_spec,
    )


def test_every_binding_kind_answers_the_mapping_it_renders() -> None:
    rendered = order_selector_spec(SelectorKind.RETRIEVE, selector=lambda result, **_: result)
    bindings: list[Any] = [
        ToolBinding(name="svc", description=None, spec=_service(rendered)),
        SelectorToolBinding(
            name="sel", description=None, spec=order_selector_spec(SelectorKind.RETRIEVE)
        ),
        ChainToolBinding(
            name="chain_svc", description=None, steps=(ChainStep("a", _service(rendered)),)
        ),
        ChainToolBinding(
            name="chain_sel",
            description=None,
            steps=(ChainStep("a", order_selector_spec(SelectorKind.RETRIEVE)),),
        ),
    ]

    assert {b.name: b.rendered_affordances for b in bindings} == {
        "svc": {"cancel": CANCEL_ORDER},
        "sel": {"cancel": CANCEL_ORDER},
        "chain_svc": {"cancel": CANCEL_ORDER},
        "chain_sel": {"cancel": CANCEL_ORDER},
    }


def test_a_service_s_own_affordances_are_never_the_answer() -> None:
    unrendered = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda result, **_: result,
        output_serializer=OrderSerializer,
    )
    bindings: list[Any] = [
        ToolBinding(name="bare", description=None, spec=_service(None)),
        ToolBinding(name="plain", description=None, spec=_service(unrendered)),
        ChainToolBinding(
            name="chain_bare", description=None, steps=(ChainStep("a", _service(None)),)
        ),
        ChainToolBinding(
            name="chain_plain", description=None, steps=(ChainStep("a", _service(unrendered)),)
        ),
    ]

    assert [b.rendered_affordances for b in bindings] == [None, None, None, None]


def test_output_all_answers_none_even_when_the_output_step_declares_them() -> None:
    """Held here rather than through ``tools/list``, where the missing single
    output serializer already makes the schema ``None`` and would hide the guard."""
    binding = ChainToolBinding(
        name="chain",
        description=None,
        steps=(ChainStep("a", order_selector_spec(SelectorKind.RETRIEVE)),),
        output_all=True,
    )

    assert binding.output_step.spec.affordances == {"cancel": CANCEL_ORDER}
    assert binding.rendered_affordances is None
