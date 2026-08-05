"""Consumer-reported ergonomics: places the package was correct but silent.

Every case here was reported by a first-party integration that lost debugging
hours to it. None was a broken guarantee — each was a convention the package
depended on and enforced nowhere, or a decision it made and never wrote down.
"""

from __future__ import annotations

import logging
import warnings

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework_services import ServiceSpec

from rest_framework_mcp.auth.backends.django_oauth_toolkit_backend import (
    DjangoOAuthToolkitBackend,
    MountedAuthorizationServerWarning,
)
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.contrib.oauth.check_oauth_url_shadowing import (
    check_oauth_url_shadowing,
)
from rest_framework_mcp.observability import get_logger, session_fingerprint
from rest_framework_mcp.server.mcp_server import MCPServer


class _AlwaysAllow:
    """Minimal MCP permission — keeps ``UnguardedToolWarning`` out of the way."""

    def has_permission(self, *_args: object, **_kwargs: object) -> bool:
        return True


def _spec() -> ServiceSpec:
    return ServiceSpec(service=lambda: {"ok": True})


class TestAuthorizationServerRoot:
    """NICE-1 — the issuer is a site root, and copying DOT's own breaks it."""

    def test_a_mounted_value_warns(self) -> None:
        with pytest.warns(MountedAuthorizationServerWarning, match="site root"):
            DjangoOAuthToolkitBackend(authorization_servers=["https://example.test/oauth"])

    def test_a_trailing_slash_does_not_hide_it(self) -> None:
        with pytest.warns(MountedAuthorizationServerWarning):
            DjangoOAuthToolkitBackend(authorization_servers=["https://example.test/oauth/"])

    def test_a_site_root_is_silent(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", MountedAuthorizationServerWarning)
            DjangoOAuthToolkitBackend(authorization_servers=["https://example.test"])

    def test_a_pathless_authorize_path_disables_the_check(self) -> None:
        """Nothing is appended, so there is no doubled segment to warn about."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", MountedAuthorizationServerWarning)
            DjangoOAuthToolkitBackend(
                authorization_servers=["https://example.test/oauth"], authorize_path="/"
            )

    def test_no_authorization_server_configured_is_silent(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", MountedAuthorizationServerWarning)
            DjangoOAuthToolkitBackend(authorization_servers=[])

    def test_the_published_metadata_is_what_the_warning_predicts(self) -> None:
        """Pin the actual damage, not just the warning about it."""
        with pytest.warns(MountedAuthorizationServerWarning):
            backend = DjangoOAuthToolkitBackend(
                authorization_servers=["https://example.test/oauth"]
            )
        metadata = backend.authorization_server_metadata()
        assert metadata.authorization_endpoint == "https://example.test/oauth/oauth/authorize/"

    def test_custom_paths_suppress_the_warning_and_are_used(self) -> None:
        """A project that really did mount DOT under a prefix is not nagged."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", MountedAuthorizationServerWarning)
            backend = DjangoOAuthToolkitBackend(
                authorization_servers=["https://example.test/oauth"],
                authorize_path="/authorize/",
                token_path="/token/",
                registration_path="/register/",
            )
        metadata = backend.authorization_server_metadata()
        assert metadata.authorization_endpoint == "https://example.test/oauth/authorize/"
        assert metadata.token_endpoint == "https://example.test/oauth/token/"


class TestStructuredOutputCouplingAtRegistration:
    """NICE-4 — a spec-violating pair fails at import, not on first traffic."""

    def _server(self, **config_kwargs: object) -> MCPServer:
        return MCPServer(name="t", config=build_mcp_config(**config_kwargs))

    def test_schema_without_content_is_refused_when_registering(self) -> None:
        server = self._server(include_output_schema=True, include_structured_content=False)
        with pytest.raises(ImproperlyConfigured, match="structuredContent"):
            server.register_service_tool(
                name="t.x", spec=_spec(), description="d", permissions=[_AlwaysAllow()]
            )

    def test_a_binding_override_can_make_the_global_pair_legal(self) -> None:
        """⚠ Why this check cannot live on the global config alone.

        Server-wide "schema on, content off" is legitimate precisely when every
        binding overrides the content back on — so checking the two settings at
        ``build_mcp_config`` time would reject a working configuration.
        """
        server = self._server(include_output_schema=True, include_structured_content=False)
        server.register_service_tool(
            name="t.ok",
            spec=_spec(),
            description="d",
            permissions=[_AlwaysAllow()],
            include_structured_content=True,
        )

    def test_the_ordinary_pair_registers_cleanly(self) -> None:
        server = self._server(include_output_schema=True, include_structured_content=True)
        server.register_service_tool(
            name="t.y", spec=_spec(), description="d", permissions=[_AlwaysAllow()]
        )


class TestOAuthUrlShadowing:
    """NICE-2 — DOT 3.4.0 contests paths we serve, and first-match wins."""

    @override_settings(ROOT_URLCONF="tests.testapp.urls")
    def test_unmounted_paths_are_not_reported(self) -> None:
        """Not serving the OAuth surface at all is a valid configuration."""
        assert check_oauth_url_shadowing(warn=False) == []

    @override_settings(ROOT_URLCONF="tests.testapp.shadowed_oauth_urls")
    def test_foreign_views_on_our_paths_are_reported(self) -> None:
        shadowed = check_oauth_url_shadowing(warn=False)
        assert "/oauth/register/" in shadowed
        assert "/.well-known/oauth-authorization-server" in shadowed

    @override_settings(ROOT_URLCONF="tests.conformance.urls")
    def test_our_own_views_on_those_paths_are_not_reported(self) -> None:
        """The correctly-ordered mount is the case that must stay quiet."""
        assert check_oauth_url_shadowing(warn=False) == []

    @override_settings(ROOT_URLCONF="tests.testapp.shadowed_oauth_urls")
    def test_the_default_is_to_warn_with_the_remedy(self, caplog) -> None:
        """The remedy is mount *order*, so the message has to say so."""
        with caplog.at_level(logging.WARNING, logger="rest_framework_mcp"):
            check_oauth_url_shadowing()
        assert any("first-match" in record.getMessage() for record in caplog.records)


class TestObservability:
    """NICE-7 — the package logged nothing at all before 0.25.0."""

    def test_a_session_id_is_never_logged_whole(self) -> None:
        full = "s3cr3t-session-identifier-value"
        tag = session_fingerprint(full)
        assert full not in tag
        assert tag.startswith("s3cr3t-s")

    def test_a_missing_session_is_distinguishable_in_the_tag(self) -> None:
        assert session_fingerprint(None) == "-"
        assert session_fingerprint("") == "-"

    def test_the_namespace_is_configurable_as_one_tree(self) -> None:
        """A project silences or raises the whole package with one entry."""
        assert get_logger("rest_framework_mcp.transport.x").name.startswith("rest_framework_mcp")

    def test_a_rejected_session_names_its_exact_cause_server_side(self, caplog) -> None:
        """⭐ The no-oracle rule constrains the response, not the log.

        The wire deliberately merges unknown-id with wrong-principal. An
        operator is not the adversary that protects against, so the log names
        which one fired — the single line that would have ended the incident
        this wave came out of on day one.
        """
        from tests.transport.test_sessionless_legacy import _post_request, _view

        with caplog.at_level(logging.WARNING, logger="rest_framework_mcp"):
            _view(sessions=True)(_post_request("ping", session_id="made-up"))
        assert any("session-unknown" in record.getMessage() for record in caplog.records)

    def test_a_missing_session_logs_the_other_cause(self, caplog) -> None:
        from tests.transport.test_sessionless_legacy import _post_request, _view

        with caplog.at_level(logging.WARNING, logger="rest_framework_mcp"):
            _view(sessions=True)(_post_request("ping"))
        assert any("session-missing" in record.getMessage() for record in caplog.records)
