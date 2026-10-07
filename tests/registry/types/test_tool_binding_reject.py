"""A service tool under ``REJECT`` is refused at registration where dispatch cannot enforce it.

drf-services refuses to run ``UnknownArguments.REJECT`` against a surface it
cannot read: a ``**kwargs`` whose annotation does not resolve at runtime, on the
target lookup or on a service spread into its parameters, raises
``ImproperlyConfigured`` on every call. ``tools/list`` asks the same question to
decide whether the schema is closed, so the binding asks it once when it is
built and the author hears about it there, rather than every listing failing.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding, UnknownArguments


def _touch(*, title: str = "", **changes: Any) -> dict[str, Any]:
    return {"title": title}


def _task_by(*, pk: int, **scope: Any) -> dict[str, Any]:
    return {"pk": pk}


# What an ``Unpack[...]`` of a ``TypedDict`` imported under ``TYPE_CHECKING``
# leaves at runtime: an annotation that names something nothing defines.
_touch.__annotations__ = {**_touch.__annotations__, "changes": "Unpack[NoSuchExtras]"}
_task_by.__annotations__ = {**_task_by.__annotations__, "scope": "Unpack[NoSuchScope]"}


class _TitleInput(serializers.Serializer):
    title = serializers.CharField()


def _register(
    spec: ServiceSpec, *, argument_binding: ArgumentBinding, unknown_arguments: UnknownArguments
) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="touch",
        spec=spec,
        argument_binding=argument_binding,
        unknown_arguments=unknown_arguments,
    )
    return server


_UNREADABLE = [
    pytest.param(
        ServiceSpec(service=_touch, atomic=False, permission_classes=[AllowAny]),
        ArgumentBinding.SPREAD_AUTHOR_WINS,
        id="spread-service",
    ),
    pytest.param(
        ServiceSpec(
            service=lambda *, instance, data: None,
            input_serializer=_TitleInput,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_task_by),
            atomic=False,
            permission_classes=[AllowAny],
        ),
        ArgumentBinding.BUNDLE,
        id="target-lookup",
    ),
]


@pytest.mark.parametrize(("spec", "argument_binding"), _UNREADABLE)
def test_reject_against_an_unreadable_surface_is_refused_at_registration(
    spec: ServiceSpec, argument_binding: ArgumentBinding
) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"Tool 'touch': UnknownArguments\.REJECT"):
        _register(
            spec, argument_binding=argument_binding, unknown_arguments=UnknownArguments.REJECT
        )


@pytest.mark.parametrize(("spec", "argument_binding"), _UNREADABLE)
@pytest.mark.parametrize("policy", [UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH])
def test_a_permissive_policy_registers_the_same_spec_open(
    spec: ServiceSpec, argument_binding: ArgumentBinding, policy: UnknownArguments
) -> None:
    # drf-services takes an unreadable surface as open under the permissive
    # policies, so there is nothing to refuse and the schema says so.
    server = _register(spec, argument_binding=argument_binding, unknown_arguments=policy)

    (tool,) = server.list_tools(user=None)["tools"]

    assert tool["inputSchema"]["additionalProperties"] is True


def test_a_service_dispatch_never_reads_is_not_asked() -> None:
    # Bundled, the service's own surface is not part of the declared set, so
    # an unreadable ``**kwargs`` there costs nothing and ``REJECT`` closes.
    server = _register(
        ServiceSpec(service=_touch, atomic=False, permission_classes=[AllowAny]),
        argument_binding=ArgumentBinding.BUNDLE,
        unknown_arguments=UnknownArguments.REJECT,
    )

    (tool,) = server.list_tools(user=None)["tools"]

    assert tool["inputSchema"]["additionalProperties"] is False


def test_a_typed_dict_declared_below_the_decorated_service_is_named_as_a_cause() -> None:
    # The decorator registers while the module is still importing, so a
    # ``TypedDict`` declared further down does not exist yet. drf-services'
    # message names only ``TYPE_CHECKING``, which this module does not use; the
    # refusal names both causes and the remedy for each.
    with pytest.raises(ImproperlyConfigured) as caught:
        importlib.import_module("tests.registry.types.forward_reference_tools")
    message = str(caught.value)
    assert "Tool 'touch': UnknownArguments.REJECT cannot be enforced" in message
    assert "imported under 'if TYPE_CHECKING:'" in message
    assert "declared below the decorated function" in message
    assert "declare it above the function, or register the tool once the module has imported" in (
        message
    )
