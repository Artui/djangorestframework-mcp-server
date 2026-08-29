"""The drf-services boundary: this package does not re-export its sister's API.

Nine symbols -- ``ServiceSpec``, ``SelectorSpec``, ``SelectorKind``,
``ServiceView`` and the five service / selector protocols -- used to be
importable from here as a convenience. Each of those imports named a *module
path* inside drf-services, so this package's public API was pinned to the other
one's internal layout: a move over there would have broken an import from here.

Pinned as a test rather than left to review, because a re-export is one line and
reads like a kindness. The kindness is the sister package's own ``__init__`` to
give, and it does.
"""

from __future__ import annotations

import pytest

import rest_framework_mcp

GONE = [
    "CreateService",
    "DeleteService",
    "ListSelector",
    "RetrieveSelector",
    "SelectorKind",
    "SelectorSpec",
    "ServiceSpec",
    "ServiceView",
    "UpdateService",
]


@pytest.mark.parametrize("name", GONE)
def test_a_drf_services_symbol_is_not_re_exported(name: str) -> None:
    assert not hasattr(rest_framework_mcp, name)
    assert name not in rest_framework_mcp.__all__


@pytest.mark.parametrize("name", GONE)
def test_it_is_importable_from_the_package_that_declares_it(name: str) -> None:
    """The migration, asserted rather than described: same symbol, one import away."""
    import rest_framework_services

    assert hasattr(rest_framework_services, name)
