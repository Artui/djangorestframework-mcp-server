from __future__ import annotations

from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.tool_binding import ToolBinding


def test_service_property_returns_spec_callable() -> None:
    def svc() -> None: ...

    binding = ToolBinding(name="t", description=None, spec=ServiceSpec(service=svc))
    assert binding.service is svc
