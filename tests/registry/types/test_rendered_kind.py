"""Whether a spec's result renders as one object or a list, asked of the function.

``rendered_kind`` picks both the advertised ``outputSchema`` shape and the chain
renderer's ``many``, so it has to give the cardinality drf-services' dispatch
gives the same spec. Each row is one branch or one conjunct of the rule; the
end-to-end shapes are ``tests/handlers/test_list_output_conforms.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.utils import rendered_kind


def _rows() -> Any:
    return []


def _same(*, result: Any) -> Any:
    return result


def _service(**_: Any) -> Any:
    return None


@pytest.mark.parametrize(
    ("spec", "kind"),
    [
        pytest.param(
            SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
            SelectorKind.LIST,
            id="list-selector",
        ),
        pytest.param(
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_rows),
            SelectorKind.RETRIEVE,
            id="retrieve-selector",
        ),
        pytest.param(
            ServiceSpec(
                service=_service,
                many=True,
                output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE),
            ),
            SelectorKind.LIST,
            id="list-payload-service",
        ),
        # Holds ``nested is not None``: without it this raises on ``None.kind``.
        pytest.param(ServiceSpec(service=_service), SelectorKind.RETRIEVE, id="no-output-spec"),
        # Holds ``nested.kind is LIST``: without it every output spec is a list.
        pytest.param(
            ServiceSpec(
                service=_service, output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE)
            ),
            SelectorKind.RETRIEVE,
            id="retrieve-output-no-re-read",
        ),
        pytest.param(
            ServiceSpec(
                service=_service,
                output_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_same),
            ),
            SelectorKind.LIST,
            id="list-re-read",
        ),
        # drf-services presents the service's own return as the set a ``LIST``
        # declaration names, so no selector is needed to make one.
        pytest.param(
            ServiceSpec(
                service=_service, output_selector_spec=SelectorSpec(kind=SelectorKind.LIST)
            ),
            SelectorKind.LIST,
            id="list-output-no-re-read",
        ),
    ],
)
def test_the_rendered_kind_is_the_one_dispatch_gives(spec: Any, kind: SelectorKind) -> None:
    assert rendered_kind(spec) is kind
