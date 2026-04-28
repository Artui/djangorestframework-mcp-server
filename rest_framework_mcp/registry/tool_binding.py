from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.output.format import OutputFormat

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")
# ``ExtraT`` mirrors the upstream ``ServiceSpec.ExtraT`` bound — providers
# always return a kwargs dict, never a non-mapping value.
ExtraT = TypeVar("ExtraT", bound=dict[str, Any])


@dataclass(frozen=True)
class ToolBinding(Generic[InputT, ResultT, ExtraT]):
    """All wiring for a single MCP tool, derived from a ``ServiceSpec``.

    A tool is the projection of a service callable plus its declared input
    and output serializers. The MCP server invokes ``spec.service`` directly
    via ``resolve_callable_kwargs`` + ``run_service`` — there is no view or
    viewset in the dispatch path.

    The ``Generic[InputT, ResultT, ExtraT]`` parameters mirror
    ``ServiceSpec``'s generics and are purely informational for type
    checkers. They default to ``Any`` when omitted, so existing call sites
    keep working unchanged.
    """

    name: str
    description: str | None
    spec: ServiceSpec[InputT, ResultT, ExtraT]
    output_format: OutputFormat = OutputFormat.JSON
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    title: str | None = None

    @property
    def service(self) -> Callable[..., ResultT]:
        return self.spec.service


__all__ = ["ToolBinding"]
