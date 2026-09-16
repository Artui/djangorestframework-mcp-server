"""Registration-time checks that a declared serializer is a shape MCP can use.

``rest_framework_mcp.adapters.utils.validate_serializer_shapes`` runs from all
three tool adapters, before anything else reads the serializer, and from the
resource adapter for its output alone (tested with the read path, in
``tests/handlers/test_resources_read_dataclass_output.py``). Without it a
misdeclared ``input_serializer`` registered cleanly and then failed on every
call -- and, from djangorestframework-services 0.50, failed ``tools/list`` for
*every* tool on the server, because this transport derives each schema per
request and upstream now refuses to derive one for it.

The two sides accept different shapes on purpose, and the tests pin both
boundaries: input is validated, which needs a ``Serializer`` subclass, while
output is only rendered, which any ``BaseSerializer`` subclass can do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.adapters.chain_to_tool import chain_steps_to_tool
from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool
from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool
from rest_framework_mcp.adapters.utils import validate_serializer_shapes
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.output_schema import build_output_schema


class _Input(drf_serializers.Serializer):
    word = drf_serializers.CharField()


class _ReadOnly(drf_serializers.BaseSerializer):
    """DRF's documented read-only serializer: renders, cannot validate."""

    def to_representation(self, instance: Any) -> dict[str, Any]:
        return {"shout": str(instance["word"]).upper()}


@dataclass
class _DC:
    word: str


class _NotASerializer:
    pass


# ---------- the helper: what each side accepts ----------


@pytest.mark.parametrize("value", [None, _Input, _DC])
def test_input_accepts_what_validation_can_run(value: object) -> None:
    validate_serializer_shapes(label="x", input_serializer=value)


@pytest.mark.parametrize("value", [None, _Input, _ReadOnly, _DC])
def test_output_accepts_what_rendering_can_call(value: object) -> None:
    validate_serializer_shapes(label="x", output_serializer=value)


@pytest.mark.parametrize("value", [None, _Input, _DC])
def test_every_admitted_input_derives_a_schema(value: object) -> None:
    """The guard is only worth having if what it admits cannot fail discovery.

    ``tools/list`` derives each schema per request, so an admitted shape whose
    derivation raised would take the whole listing down. That is decided
    upstream, which is why it is pinned here: a drf-services release narrowing
    either rule fails this before it fails a server.
    """
    build_input_schema(value)


@pytest.mark.parametrize("value", [None, _Input, _ReadOnly, _DC])
def test_every_admitted_output_derives_a_schema(value: object) -> None:
    build_output_schema(value)


def test_input_refuses_an_unrelated_class() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^x: input_serializer must be a DRF") as exc:
        validate_serializer_shapes(label="x", input_serializer=_NotASerializer)
    assert "_NotASerializer" in str(exc.value)
    # The instance hint is for instances only; on a class it would mislead.
    assert "not an instance" not in str(exc.value)


def test_input_refuses_a_serializer_instance_and_says_to_pass_the_class() -> None:
    with pytest.raises(ImproperlyConfigured, match="Pass the class itself, not an instance"):
        validate_serializer_shapes(label="x", input_serializer=_Input(many=True))


def test_input_refuses_a_dataclass_instance() -> None:
    with pytest.raises(ImproperlyConfigured, match="Pass the class itself, not an instance"):
        validate_serializer_shapes(label="x", input_serializer=_DC(word="hi"))


def test_input_refuses_a_read_only_serializer_that_output_accepts() -> None:
    """The boundary between the two sides: rendering is not validation."""
    with pytest.raises(ImproperlyConfigured, match="input_serializer must be a DRF"):
        validate_serializer_shapes(label="x", input_serializer=_ReadOnly)


def test_output_refuses_an_unrelated_class() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^x: output serializer must be a DRF"):
        validate_serializer_shapes(label="x", output_serializer=_NotASerializer)


def test_output_refuses_a_serializer_instance() -> None:
    with pytest.raises(ImproperlyConfigured, match="Pass the class itself, not an instance"):
        validate_serializer_shapes(label="x", output_serializer=_Input())


# ---------- wiring through the adapters ----------


def test_service_adapter_refuses_input_before_the_parameter_checks() -> None:
    """Asserts *which* check answers, not just that one did.

    Under a spread binding the callable-parameter check runs against the
    serializer's fields, and an unrelated class has none -- so without the shape
    check running first, this registration fails anyway, blaming the service's
    ``word`` parameter for having no source.
    """

    def service(*, word: str) -> None: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured) as exc:
        service_spec_to_tool(
            name="t",
            spec=ServiceSpec(service=service, input_serializer=_NotASerializer, atomic=False),
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
    assert str(exc.value).startswith("service tool 't': input_serializer must be a DRF")
    assert "no static source" not in str(exc.value)


def test_service_adapter_refuses_output_selector_serializer() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^service tool 't': output serializer"):
        service_spec_to_tool(
            name="t",
            spec=ServiceSpec(
                service=lambda **_: None,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=_NotASerializer
                ),
            ),
        )


def test_selector_adapter_refuses_input_serializer_instance() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^selector tool 's': input_serializer"):
        selector_spec_to_tool(
            name="s",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda **_: []),
            input_serializer=_Input(many=True),
        )


def test_selector_adapter_refuses_output_serializer() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^selector tool 's': output serializer"):
        selector_spec_to_tool(
            name="s",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=lambda **_: [],
                output_serializer=_NotASerializer,
            ),
        )


def _service_step(
    alias: str, *, input_serializer: object = None, output_serializer: object = None
) -> ChainStep:
    return ChainStep(
        alias,
        ServiceSpec(
            service=lambda **_: {},
            atomic=False,
            input_serializer=input_serializer,
            output_selector_spec=(
                SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=output_serializer)
                if output_serializer is not None
                else None
            ),
        ),
    )


def test_chain_adapter_refuses_explicit_input_serializer() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^chain tool 'c': input_serializer"):
        chain_steps_to_tool(name="c", steps=(_service_step("a"),), input_serializer=_NotASerializer)


def test_chain_adapter_refuses_input_serializer_inherited_from_first_step() -> None:
    """A chain with no explicit input validates against its first step's."""
    with pytest.raises(ImproperlyConfigured, match=r"^chain tool 'c': input_serializer"):
        chain_steps_to_tool(name="c", steps=(_service_step("a", input_serializer=_NotASerializer),))


def test_chain_adapter_refuses_the_output_steps_serializer() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^chain tool 'c', step 'b': output"):
        chain_steps_to_tool(
            name="c",
            steps=(_service_step("a"), _service_step("b", output_serializer=_NotASerializer)),
        )


def test_chain_adapter_ignores_a_step_serializer_that_never_renders() -> None:
    """Only the output step renders, so only its serializer is held to the shape."""
    binding = chain_steps_to_tool(
        name="c",
        steps=(_service_step("a", output_serializer=_NotASerializer), _service_step("b")),
    )
    assert binding.output_step.alias == "b"


def test_chain_adapter_under_output_all_refuses_every_rendered_step() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"^chain tool 'c', step 'a': output"):
        chain_steps_to_tool(
            name="c",
            steps=(_service_step("a", output_serializer=_NotASerializer), _service_step("b")),
            output_all=True,
        )


# ---------- end to end through the server ----------


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend())


def test_registration_refuses_the_tool_that_used_to_break_discovery() -> None:
    """The consequence the check exists for, on the public registration API.

    Before it, this registered cleanly; ``tools/list`` then raised for the whole
    server on djangorestframework-services 0.50, and advertised a tool taking
    no arguments on earlier releases.
    """
    server = _server()
    server.register_service_tool(
        name="good",
        spec=ServiceSpec(service=lambda **_: {}, input_serializer=_Input, atomic=False),
    )
    with pytest.raises(ImproperlyConfigured, match="service tool 'bad'"):
        server.register_service_tool(
            name="bad",
            spec=ServiceSpec(service=lambda **_: {}, input_serializer=_Input(), atomic=False),
        )
    listing: Any = server.list_tools(user=None)
    assert [tool["name"] for tool in listing["tools"]] == ["good"]


async def test_a_read_only_output_serializer_still_registers_and_renders() -> None:
    """The check must not refuse what renders: a ``BaseSerializer`` subclass does."""
    server = _server()
    server.register_service_tool(
        name="shout",
        spec=ServiceSpec(
            service=lambda **_: {"word": "hi"},
            atomic=False,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, output_serializer=_ReadOnly
            ),
        ),
    )
    result: Any = await server.acall_tool("shout", user=None)
    assert result["structuredContent"] == {"shout": "HI"}


async def test_a_dataclass_output_registers_and_renders() -> None:
    """The other shape output admits that input's rule would not reach.

    drf-services' renderer resolves a dataclass to a ``DataclassSerializer``;
    before it did, this registered, advertised a schema, and raised on every call
    because the dataclass's own ``__init__`` was handed ``many=``.
    """
    server = _server()
    server.register_service_tool(
        name="echo",
        spec=ServiceSpec(
            service=lambda **_: _DC(word="hi"),
            atomic=False,
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_DC),
        ),
    )
    result: Any = await server.acall_tool("echo", user=None)
    assert result["structuredContent"] == {"word": "hi"}
