"""Every binding kind answers ``output_serializer``, and means the same thing.

The name used to be ``agent_output_serializer`` on three of the four, which was
a leak by the rule drf-services 0.48.0 settled: "agent" is earned where a name
marks an audience the serializer author declares, and a leak where it marks only
which callers happen to use it. Nothing about *which serializer produces the
output* is agent-specific -- there is no non-agent variant of it.

It was also one idea spelled twice. ``ChainToolBinding`` already carried an
``output_serializer``, and its ``agent_output_serializer`` was
``return self.output_serializer`` -- so unifying deleted a member rather than
renaming one. These bindings share no base class, so that they agree at all is a
test rather than a signature.
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
from tests.testapp.serializers import InvoiceOutputSerializer


def _out_spec(**kw: Any) -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer, **kw)


def test_every_binding_kind_answers_to_the_same_name() -> None:
    service = ServiceSpec(service=lambda **_: {}, output_selector_spec=_out_spec())
    bindings: list[Any] = [
        ToolBinding(name="svc", description=None, spec=service),
        SelectorToolBinding(
            name="sel",
            description=None,
            spec=SelectorSpec(
                selector=lambda **_: [],
                kind=SelectorKind.LIST,
                output_serializer=InvoiceOutputSerializer,
            ),
        ),
        ChainToolBinding(name="chain", description=None, steps=(ChainStep("a", service),)),
    ]

    resolved = {type(b).__name__: b.output_serializer for b in bindings}

    assert resolved == {
        "ToolBinding": InvoiceOutputSerializer,
        "SelectorToolBinding": InvoiceOutputSerializer,
        "ChainToolBinding": InvoiceOutputSerializer,
    }


def test_the_leaked_spelling_is_gone() -> None:
    """A stale caller should fail loudly, not find a second copy of the concept."""
    binding = ToolBinding(
        name="svc",
        description=None,
        spec=ServiceSpec(service=lambda **_: {}, output_selector_spec=_out_spec()),
    )

    assert not hasattr(binding, "agent_output_serializer")
