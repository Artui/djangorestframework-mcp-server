"""Which specs can render a successful call to nothing, and so admit ``{}``.

``can_present_nothing`` decides whether a tool's ``outputSchema`` moves its
``required`` list into an ``anyOf`` beside the empty object, so a wrong ``True``
loosens a schema every typed client reads (every row field turns optional) and
a wrong ``False`` advertises a schema the served ``{}`` fails. Each case here is
one branch of the rule, asked of the function directly, because the schema
builder also refuses to rewrite a ``LIST`` schema and so cannot tell from
outside which of the two answered.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.utils import can_present_nothing
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer


def _all() -> Any:
    return Invoice.objects.all()


def _by_ids(*, ids: list[int]) -> Any:
    return Invoice.objects.filter(pk__in=ids)


def _unsent(*, instance: Invoice) -> Any:
    return Invoice.objects.filter(pk=instance.pk, sent=False)


def _same(*, instance: Any) -> Any:
    return instance


def _service(*, data: dict[str, Any]) -> Any:
    return None


def _out(**kwargs: Any) -> SelectorSpec:
    return SelectorSpec(output_serializer=InvoiceOutputSerializer, **kwargs)


def _spec(**kwargs: Any) -> ServiceSpec:
    return ServiceSpec(service=_service, atomic=False, permission_classes=[AllowAny], **kwargs)


def test_a_service_with_an_output_reread_can_present_nothing() -> None:
    # dispatch materializes the re-read with ``.first()``, so a re-read that
    # filters out the row the service returned yields nothing.
    spec = _spec(output_selector_spec=_out(kind=SelectorKind.RETRIEVE, selector=_unsent))

    assert can_present_nothing(spec) is True


def test_a_service_rendering_its_own_return_keeps_its_schema_strict() -> None:
    # No re-read selector: the output spec only names the serializer the
    # service's own return renders through, which is the common case.
    spec = _spec(output_selector_spec=_out(kind=SelectorKind.RETRIEVE))

    assert can_present_nothing(spec) is False


def test_a_service_with_no_output_spec_at_all_cannot_present_nothing() -> None:
    assert can_present_nothing(_spec()) is False


@pytest.mark.parametrize(
    "output",
    [
        pytest.param(None, id="no-output-spec"),
        pytest.param(_out(kind=SelectorKind.RETRIEVE), id="no-re-read"),
    ],
)
def test_a_service_declaring_allow_none_can_present_nothing(output: Any) -> None:
    # Its own return is what it presents, and ``allow_none=True`` says that
    # return may be ``None``. The same spec undeclared is the two tests above.
    spec = _spec(allow_none=True, output_selector_spec=output)

    assert can_present_nothing(spec) is True


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_all,
                allow_none=True,
                output_serializer=InvoiceOutputSerializer,
                permission_classes=[AllowAny],
            ),
            id="allow-none-list-selector",
        ),
        pytest.param(
            _spec(
                collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_by_ids),
                output_selector_spec=_out(kind=SelectorKind.LIST, selector=_same),
            ),
            id="service-rereading-a-list",
        ),
        pytest.param(
            _spec(allow_none=True, output_selector_spec=_out(kind=SelectorKind.LIST)),
            id="allow-none-service-presenting-its-return-as-a-list",
        ),
    ],
)
def test_a_list_result_never_presents_nothing(spec: Any) -> None:
    # Each spec here would answer ``True`` were its result one row: an
    # ``allow_none`` selector, a service with a re-read selector and a service
    # declaring ``allow_none``. A list result is a list, empty at worst.
    assert can_present_nothing(spec) is False


@pytest.mark.parametrize("allow_none", [True, False])
def test_a_retrieve_selector_presents_nothing_only_under_allow_none(allow_none: bool) -> None:
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=_all,
        allow_none=allow_none,
        output_serializer=InvoiceOutputSerializer,
        permission_classes=[AllowAny],
    )

    assert can_present_nothing(spec) is allow_none
