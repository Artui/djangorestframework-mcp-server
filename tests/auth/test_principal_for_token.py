"""``principal_for_token`` — who owns a session, and who must not share one.

The happy paths are covered alongside the transport that consumes them; this
file is about the boundary between *deliberately anonymous* and *authenticated
but unnameable*, which used to be one case and is now two.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.auth.principal_for_token import principal_for_token
from rest_framework_mcp.auth.types.token_info import TokenInfo

# --- an authenticated caller with no primary key -----------------------------


class _Nameless:
    """A resolved principal a backend might plausibly return: no ``pk``.

    A service-account object, a JWT-claim wrapper, a custom principal class.
    Nothing about it is obviously wrong, which is the problem.
    """

    is_authenticated = True


class _Undeclared:
    """Declares neither ``pk`` nor ``is_authenticated`` — the ambiguous case."""


def test_an_authenticated_caller_with_no_pk_is_refused() -> None:
    """⛔ The security content of this function.

    Sessions and tasks key on the principal id. Two distinct authenticated
    callers resolving to ``"anonymous"`` can each present the other's session id
    and read the other's task results — and every request succeeds while it
    happens, so the only symptom is that isolation is absent.
    """
    with pytest.raises(ImproperlyConfigured) as excinfo:
        principal_for_token(TokenInfo(user=_Nameless(), scopes=()))

    message = str(excinfo.value)
    # Names the offending class and both remedies — an operator hitting this is
    # one line from correct.
    assert "_Nameless" in message
    assert "pk" in message
    assert "AnonymousUser" in message


def test_a_user_declaring_neither_is_refused_too() -> None:
    """Ambiguity resolves to refusal, not to sharing.

    A ``str``, a ``SimpleNamespace``, a dataclass — anything a backend returns
    that declares neither identity nor anonymity. Treating it as anonymous is
    the collapse; treating it as authenticated-but-unnameable is the honest read.
    """
    with pytest.raises(ImproperlyConfigured):
        principal_for_token(TokenInfo(user=_Undeclared(), scopes=()))


def test_a_deliberately_anonymous_caller_still_shares_the_principal() -> None:
    """⚠ Not a regression — sharing is correct when nobody was identified.

    ``AllowAnyBackend`` mints exactly this, and the docstring has always said
    ownership is only as strong as the backend behind it. The fix separates
    "nobody was identified" from "somebody was, and we cannot name them"; only
    the second is refused.
    """
    assert principal_for_token(TokenInfo(user=AnonymousUser(), scopes=())) == "anonymous"


def test_a_token_with_no_user_at_all_is_anonymous() -> None:
    assert principal_for_token(TokenInfo(user=None, scopes=())) == "anonymous"
