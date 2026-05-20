"""Unit tests for the shared kwarg-pool builder.

The integration paths (service-tool dispatch, selector-tool dispatch)
are covered in their own files; these tests pin the wire shape of the
pool dict directly so regressions surface here before they show up as
"weird kwargs reach the callable" failures downstream.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.handlers.build_call_pool import build_call_pool
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


def _binding(
    *,
    argument_binding: ArgumentBinding,
    kwargs_provider: Any = None,
) -> ToolBinding:
    return ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=lambda: None, atomic=False, kwargs=kwargs_provider),
        argument_binding=argument_binding,
    )


def test_data_only_carries_data_and_provider_only() -> None:
    binding = _binding(argument_binding=ArgumentBinding.DATA_ONLY)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated={"x": 1},
        arguments_raw={"x": 1},
    )
    assert set(pool) == {"request", "user", "data"}
    assert pool["data"] == {"x": 1}


def test_data_only_carries_data_none_when_no_validator() -> None:
    binding = _binding(argument_binding=ArgumentBinding.DATA_ONLY)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated=None,
        arguments_raw={"x": 1},
    )
    assert pool["data"] is None


def test_merge_spreads_validated_keys() -> None:
    binding = _binding(argument_binding=ArgumentBinding.MERGE)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated={"project_id": "p1", "limit": 10},  # ``limit`` is reserved
        arguments_raw={"project_id": "p1", "limit": 10},
    )
    # ``limit`` (a pipeline-reserved key) is filtered out of the spread.
    assert "project_id" in pool
    assert "limit" not in pool
    # ``data`` is preserved alongside the spread.
    assert pool["data"] == {"project_id": "p1", "limit": 10}


def test_merge_drops_reserved_pool_seeds_from_spread() -> None:
    binding = _binding(argument_binding=ArgumentBinding.MERGE)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user="real-user",
        validated={"user": "evil", "request": "evil", "data": "evil", "ok": 1},
        arguments_raw={},
    )
    # Reserved pool seeds win — client cannot poison them.
    assert pool["user"] == "real-user"
    assert isinstance(pool["request"], HttpRequest)
    # ``data`` is set from the validated parameter (it isn't the spread's
    # ``data`` key — that was filtered out).
    assert pool["data"]["ok"] == 1  # type: ignore[index]
    assert pool["ok"] == 1


def test_merge_provider_overrides_spread() -> None:
    def provider(view: Any, request: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"project_id": "from-provider"}

    binding = _binding(argument_binding=ArgumentBinding.MERGE, kwargs_provider=provider)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated={"project_id": "from-client"},
        arguments_raw={"project_id": "from-client"},
    )
    # MERGE: provider wins on conflict — author-declared kwargs trump
    # client-supplied ones (project-scoping invariant).
    assert pool["project_id"] == "from-provider"


def test_replace_spread_overrides_provider() -> None:
    def provider(view: Any, request: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"page_size": 50}  # default

    binding = _binding(argument_binding=ArgumentBinding.REPLACE, kwargs_provider=provider)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated={"page_size": 200},
        arguments_raw={"page_size": 200},
    )
    # REPLACE: spread wins on conflict — provider supplies defaults the
    # client may customise.
    assert pool["page_size"] == 200


def test_replace_provider_alone_when_no_overlap() -> None:
    def provider(view: Any, request: Any) -> dict[str, Any]:  # noqa: ARG001
        return {"tenant_id": 42}

    binding = _binding(argument_binding=ArgumentBinding.REPLACE, kwargs_provider=provider)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated={"other": "x"},
        arguments_raw={"other": "x"},
    )
    assert pool["tenant_id"] == 42
    assert pool["other"] == "x"


def test_merge_spread_falls_back_to_raw_when_no_validator() -> None:
    """Selectors without ``input_serializer`` spread the raw arguments."""
    binding = _binding(argument_binding=ArgumentBinding.MERGE)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated=None,
        arguments_raw={"project_id": "p1"},
    )
    assert pool["project_id"] == "p1"


def test_merge_with_non_dict_validated_uses_raw_for_spread() -> None:
    """Bare-dataclass instances aren't dicts — spread the raw arguments instead."""

    class _DC:
        pass

    binding = _binding(argument_binding=ArgumentBinding.MERGE)
    pool = build_call_pool(
        binding,
        drf_request=HttpRequest(),
        user=None,
        validated=_DC(),
        arguments_raw={"project_id": "p1"},
    )
    assert pool["project_id"] == "p1"
    assert isinstance(pool["data"], _DC)
