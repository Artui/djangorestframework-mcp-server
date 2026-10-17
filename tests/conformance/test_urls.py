"""The conformance URL conf hands every test a server of its own.

``@pytest.mark.urls("tests.conformance.urls")`` names a module, and Python
imports a module once: a ``urlpatterns`` built at import is one ``MCPServer``,
with one session store, shared by every conformance test in the process and by
any other test mounting the same conf. A session minted in one test was still
honoured in the next, so a test could pass on state an earlier one left behind.
"""

from __future__ import annotations

import pytest
from django.urls import clear_url_caches

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db
def test_a_session_minted_before_the_url_caches_clear_is_unknown_after(
    jsonrpc, initialized_session: str
) -> None:
    # What pytest-django does between two tests marked with the same conf: it
    # clears the URL caches, and the next request resolves the conf afresh.
    clear_url_caches()

    response = jsonrpc("tools/list", {}, session_id=initialized_session)

    # A fresh server has never issued that id, so it is unknown: a 404, as
    # ``test_an_unknown_session_id_is_404_not_400`` pins for any id it never issued.
    assert response.status_code == 404, response.content
