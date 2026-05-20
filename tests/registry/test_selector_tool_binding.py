from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


def _sel() -> list[dict[str, str]]:
    return []


def test_rejects_schema_on_with_content_off_at_construction() -> None:
    with pytest.raises(ImproperlyConfigured) as excinfo:
        SelectorToolBinding(
            name="bad",
            description=None,
            spec=SelectorSpec(selector=_sel),
            include_output_schema=True,
            include_structured_content=False,
        )
    assert "bad" in str(excinfo.value)


def test_allows_schema_off_with_content_on() -> None:
    binding = SelectorToolBinding(
        name="t",
        description=None,
        spec=SelectorSpec(selector=_sel),
        include_output_schema=False,
        include_structured_content=True,
    )
    assert binding.include_output_schema is False
    assert binding.include_structured_content is True
