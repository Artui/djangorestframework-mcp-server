"""A deployment that could satisfy the audience MUST and has not is told so.

The MCP ``2026-07-28`` spec requires a resource server to validate that a token
was issued for it, and the failure mode of not doing so is cross-resource token
replay. The default stays off because the ``[oauth]`` extra floors DOT at
``>=2.3``, where no token records a resource -- but that reason expires **per
deployment**, when a project upgrades DOT, and it expires silently.
"""

from __future__ import annotations

import warnings

import pytest

from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
    UnenforcedAudienceWarning,
)


def _build(**kwargs: object) -> DjangoOAuthToolkitBackend:
    return DjangoOAuthToolkitBackend(**kwargs)  # type: ignore[arg-type]


class TestWhenItFires:
    def test_enforcement_off_on_a_deployment_that_could_enforce(self) -> None:
        # DOT 3.4.0+ records the resource on stock AccessToken, so this
        # deployment is able to conform and has chosen not to.
        with pytest.warns(UnenforcedAudienceWarning, match="could enforce it"):
            _build(resource_url="https://example.test/mcp", enforce_audience=False)

    def test_the_message_names_the_setting_and_the_way_out(self) -> None:
        with pytest.warns(UnenforcedAudienceWarning) as caught:
            _build(resource_url="https://example.test/mcp", enforce_audience=False)

        said = str(caught[0].message)
        assert "ENFORCE_AUDIENCE" in said
        assert "UnenforcedAudienceWarning" in said


class TestWhenItStaysQuiet:
    def test_not_when_enforcement_is_already_on(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnenforcedAudienceWarning)
            _build(resource_url="https://example.test/mcp", enforce_audience=True)

    def test_not_without_a_resource_url(self) -> None:
        # Nothing to enforce against, so there is no conformance gap to name --
        # and a server with no resource URL has a different problem, which RFC
        # 9728 metadata already reports.
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnenforcedAudienceWarning)
            _build(enforce_audience=False)

    def test_not_on_a_dot_that_could_not_enforce_anyway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole point of the condition. Below DOT 3.4.0, or with a swapped
        # model that drops the field, enforcement is impossible -- and nagging a
        # deployment about a setting it cannot satisfy is how a warning gets
        # filtered out wholesale, taking the useful case with it.
        class _Field:
            name = "expires"

        class _Meta:
            @staticmethod
            def get_fields() -> list[_Field]:
                return [_Field()]

        class _NoResourceToken:
            __name__ = "_NoResourceToken"
            _meta = _Meta()

        monkeypatch.setattr(
            "oauth2_provider.models.get_access_token_model", lambda: _NoResourceToken
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error", UnenforcedAudienceWarning)
            _build(resource_url="https://example.test/mcp", enforce_audience=False)

    def test_it_can_be_silenced_by_a_project_that_decided_against_it(self) -> None:
        # A single-resource server a project fully controls is a legitimate
        # place to skip enforcement, which is why this warns rather than raises
        # or flips the default.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UnenforcedAudienceWarning)
            backend = _build(resource_url="https://example.test/mcp", enforce_audience=False)

        assert backend is not None
