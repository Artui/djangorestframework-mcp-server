"""The whole thing, once: register → authorize → token → connect → list → call.

Every other suite authenticates with `AllowAnyBackend`, so until now **no test
drove the MCP transport with a real OAuth token**. That is the gap three
consecutive consumer-reported blockers came through: DCR issuing credentials that
could not authenticate (0.19.0), DCR clients that could not be issued an ID token
(0.20.0), and audience enforcement that rejected every token (0.21.0). Each was
individually invisible because the legs were only ever tested apart.

So this walks one credential the entire way, against the real URL conf, DOT's own
authorize and token views, and `DjangoOAuthToolkitBackend` with a resource URL
configured — the combination a production deployment actually runs:

1.  `POST /oauth/register/` — RFC 7591, the public-PKCE shape Claude sends.
2.  `GET /mcp/` unauthenticated — 401 carrying the PRM pointer.
3.  `GET /oauth/authorize/` — DOT's view, logged-in user, real redirect + code.
4.  `POST /o/token/` — DOT's view, PKCE verifier, no client secret.
5.  `initialize` — session established with the bearer.
6.  `tools/list` — the tool is advertised.
7.  `tools/call` — it runs, and scope enforcement is real.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import types
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import include, path
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import JsonRpcErrorCode, MCPServer, ScopeRequired
from rest_framework_mcp.contrib.oauth import build_oauth_urlpatterns
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice

RESOURCE_URL = "https://testserver/mcp/"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
PROTOCOL_VERSION = "2025-11-25"
SCOPES = {"mcp:read": "Read over MCP", "mcp:write": "Mutate over MCP"}

# The authorize leg needs a real logged-in user, which needs sessions + auth.
# The default test settings run with no middleware at all.
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]


class _IssueInvoiceInput(drf_serializers.Serializer):
    number = drf_serializers.CharField(help_text="Human-facing invoice number, e.g. INV-1.")
    amount_cents = drf_serializers.IntegerField(help_text="Total in minor units, not currency.")


def _issue_invoice(*, data: dict[str, Any]) -> dict[str, Any]:
    invoice = Invoice.objects.create(number=data["number"], amount_cents=data["amount_cents"])
    return {"id": invoice.pk, "number": invoice.number, "amount_cents": invoice.amount_cents}


def _build_server() -> MCPServer:
    """A server on the real OAuth backend — no `auth_backend=` means DOT's.

    `resource_url=` is the configuration that made 0.20.0 reject every token,
    so it is deliberately set here rather than left off to keep the test simple.
    """
    server = MCPServer(
        name="oauth-flow",
        resource_url=RESOURCE_URL,
        session_store=InMemorySessionStore(),
    )
    server.register_service_tool(
        name="issue_invoice",
        spec=ServiceSpec(service=_issue_invoice, input_serializer=_IssueInvoiceInput),
        description="Issue an invoice for a customer.",
        permissions=[ScopeRequired(["mcp:write"])],
    )
    return server


def _urlconf() -> types.ModuleType:
    module = types.ModuleType("tests.conformance._oauth_flow_urls")
    server = _build_server()
    module.urlpatterns = [  # type: ignore[attr-defined]
        path("mcp/", server.urls),
        *build_oauth_urlpatterns(
            server=server,
            include_dcr=True,
            dcr_enabled=True,
            # The authorize leg goes through *our* passthrough, not DOT's own URL,
            # so the user-adapter hook is in the path a real deployment uses.
            include_authorize=True,
        ),
        path("o/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    ]
    return module


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def _rpc(
    client: Client, method: str, params: dict[str, Any] | None = None, **headers: str
) -> tuple[int, dict[str, Any], Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post(
        "/mcp/",
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )
    payload = json.loads(response.content) if response.content else {}
    return response.status_code, payload, response


@pytest.fixture
def flow_settings(settings: Any) -> None:
    settings.MIDDLEWARE = MIDDLEWARE
    # Signed-cookie sessions so `django.contrib.sessions` needn't be installed
    # just for this flow — the DB backend would want a table the shared test
    # database was never migrated for.
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.OAUTH2_PROVIDER = {"SCOPES": SCOPES, "PKCE_REQUIRED": True}
    settings.REST_FRAMEWORK_MCP = {
        "ALLOWED_ORIGINS": ["*"],
        "RESOURCE_URL": RESOURCE_URL,
        "DCR_ENABLED": True,
        "SERVER_INFO": {
            "name": "oauth-flow",
            "resource": RESOURCE_URL,
            "authorization_servers": ["https://testserver/"],
            "scopes_supported": sorted(SCOPES),
            "resource_metadata_url": f"{RESOURCE_URL}.well-known/oauth-protected-resource",
        },
    }
    # Built *last*, on purpose: the auth backend resolves SERVER_INFO once in its
    # constructor so two servers in one project can differ. Building the URL conf
    # before assigning the settings captures whatever was there before, which is
    # the documented contract rather than a bug — and an easy way to write a test
    # that silently asserts against an empty payload.
    settings.ROOT_URLCONF = _urlconf()


def _register_client(client: Client) -> dict[str, Any]:
    """Step 1 — RFC 7591, the public-PKCE shape Claude's connectors send."""
    response = client.post(
        "/oauth/register/",
        data=json.dumps(
            {
                "client_name": "Claude",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "mcp:read mcp:write",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    body = json.loads(response.content)
    # A public client gets no secret — it must be able to finish on PKCE alone.
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body
    return body


def _authorize(client: Client, *, client_id: str, challenge: str) -> str:
    """Step 3 — DOT's own authorize view, with a genuinely logged-in user."""
    user = get_user_model().objects.create_user(username="alice", password="pw")  # noqa: S106
    client.force_login(user)

    # skip_authorization is False on a DCR-created client, so DOT would render a
    # consent form. Approving it via POST is the same code path the form submits.
    response = client.post(
        "/oauth/authorize/",
        data={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "mcp:read mcp:write",
            "state": "opaque-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "allow": "Authorize",
        },
    )
    assert response.status_code == 302, getattr(response, "content", b"")
    query = parse_qs(urlparse(response["Location"]).query)
    assert query["state"] == ["opaque-state"]
    return query["code"][0]


def _exchange(client: Client, *, client_id: str, code: str, verifier: str) -> str:
    """Step 4 — DOT's token view. No secret: a public client authenticates by PKCE."""
    response = client.post(
        "/o/token/",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body["token_type"].lower() == "bearer"
    assert set(body["scope"].split()) == {"mcp:read", "mcp:write"}
    return body["access_token"]


@pytest.mark.django_db(transaction=True)
def test_a_client_can_walk_discovery_to_the_registration_endpoint(flow_settings) -> None:
    """The leg before the flow: everything a client needs before it has a token.

    A host that cannot walk PRM → AS metadata → `registration_endpoint` never
    reaches DCR, and every later leg is unreachable. Asserted as a walk rather
    than three independent endpoint tests, because it is the *chaining* that
    breaks — a payload can be individually valid and still point nowhere.
    """
    client = Client()

    # A well-formed request: envelope validation runs *before* authentication, so
    # an empty body would 400 and never reach the challenge.
    status, _, unauth = _rpc(client, "initialize")
    assert status == 401
    prm_url = unauth["WWW-Authenticate"].split('resource_metadata="', 1)[1].split('"', 1)[0]

    prm = client.get(urlparse(prm_url).path)
    assert prm.status_code == 200, prm.content
    prm_body = json.loads(prm.content)
    assert prm_body["resource"] == RESOURCE_URL
    assert prm_body["authorization_servers"] == ["https://testserver/"]
    assert set(prm_body["scopes_supported"]) == set(SCOPES)

    as_metadata = client.get("/.well-known/oauth-authorization-server")
    assert as_metadata.status_code == 200, as_metadata.content
    as_body = json.loads(as_metadata.content)
    assert as_body["registration_endpoint"].endswith("/oauth/register/")
    assert "none" in as_body["token_endpoint_auth_methods_supported"]
    assert "S256" in as_body["code_challenge_methods_supported"]

    # And the endpoint it names actually registers, rather than 404-ing or
    # refusing — the reachability-vs-usability gap a consumer hit head-on.
    registration = client.post(
        "/oauth/register/",
        data=json.dumps({"redirect_uris": [REDIRECT_URI], "token_endpoint_auth_method": "none"}),
        content_type="application/json",
    )
    assert registration.status_code == 201, registration.content


@pytest.mark.django_db(transaction=True)
def test_the_session_survives_calls_and_dies_on_delete(flow_settings) -> None:
    """Session lifecycle across the real transport, on a real token.

    `initialize` → reuse → `DELETE` → reuse-is-refused. The existing end-to-end
    suite covers this on `AllowAnyBackend`; doing it on a bearer proves the
    session and the credential are independent, so a client is not silently
    re-authenticating per call.
    """
    client = Client()
    registration = _register_client(client)
    verifier, challenge_param = _pkce()
    code = _authorize(client, client_id=registration["client_id"], challenge=challenge_param)
    token = _exchange(client, client_id=registration["client_id"], code=code, verifier=verifier)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    _, _, initialized = _rpc(
        client,
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "p"}},
        **auth,
    )
    session_id = initialized["Mcp-Session-Id"]
    session = {
        "HTTP_MCP_SESSION_ID": session_id,
        "HTTP_MCP_PROTOCOL_VERSION": PROTOCOL_VERSION,
        **auth,
    }

    # Reused across two calls without re-initializing.
    for _ in range(2):
        status, payload, _ = _rpc(client, "tools/list", {}, **session)
        assert status == 200, payload

    terminated = client.delete("/mcp/", **session)
    assert terminated.status_code == 204

    status, payload, _ = _rpc(client, "tools/list", {}, **session)
    assert status == 404, payload

    # The token is still perfectly good — only the session went away.
    _, payload, reinitialized = _rpc(
        client,
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "p"}},
        **auth,
    )
    assert reinitialized["Mcp-Session-Id"] != session_id


@pytest.mark.django_db(transaction=True)
def test_full_oauth_flow_register_authorize_connect_list_and_call(flow_settings) -> None:
    client = Client()

    registration = _register_client(client)
    client_id: str = registration["client_id"]

    # Step 2 — unauthenticated, and the 401 has to point at PRM or a client
    # cannot discover where to authenticate.
    status, _, unauth = _rpc(client, "initialize")
    assert status == 401
    challenge = unauth["WWW-Authenticate"]
    assert "resource_metadata=" in challenge

    verifier, challenge_param = _pkce()
    code = _authorize(client, client_id=client_id, challenge=challenge_param)
    access_token = _exchange(client, client_id=client_id, code=code, verifier=verifier)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}

    # Step 5 — the bearer now authenticates against DjangoOAuthToolkitBackend
    # with a resource URL configured. This is the leg that returned 401 for every
    # token before 0.21.0.
    status, payload, response = _rpc(
        client,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest-e2e", "version": "0.0"},
        },
        **auth,
    )
    assert status == 200, payload
    assert payload["result"]["protocolVersion"] == PROTOCOL_VERSION
    session_id: str = response["Mcp-Session-Id"]
    session = {
        "HTTP_MCP_SESSION_ID": session_id,
        "HTTP_MCP_PROTOCOL_VERSION": PROTOCOL_VERSION,
        **auth,
    }

    # Step 6 — the tool is advertised, with the description that makes it usable.
    status, payload, _ = _rpc(client, "tools/list", {}, **session)
    assert status == 200, payload
    tools = {tool["name"]: tool for tool in payload["result"]["tools"]}
    assert "issue_invoice" in tools
    assert tools["issue_invoice"]["description"] == "Issue an invoice for a customer."

    # Step 7 — it actually runs, and actually writes.
    status, payload, _ = _rpc(
        client,
        "tools/call",
        {"name": "issue_invoice", "arguments": {"number": "INV-1", "amount_cents": 4200}},
        **session,
    )
    assert status == 200, payload
    result = payload["result"]
    assert result.get("isError") is not True
    assert result["structuredContent"]["number"] == "INV-1"
    assert Invoice.objects.filter(number="INV-1").exists()


@pytest.mark.django_db(transaction=True)
def test_a_token_without_the_required_scope_is_refused_at_the_tool(flow_settings) -> None:
    """Same flow, narrower grant: authentication succeeds, authorisation doesn't.

    Worth its own pass because a 401-for-everything bug looks identical to
    correct scope enforcement from the outside — this pins the difference.
    """
    client = Client()
    registration = _register_client(client)
    client_id: str = registration["client_id"]
    verifier, challenge_param = _pkce()

    user = get_user_model().objects.create_user(username="bob", password="pw")  # noqa: S106
    client.force_login(user)
    response = client.post(
        "/oauth/authorize/",
        data={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "mcp:read",
            "code_challenge": challenge_param,
            "code_challenge_method": "S256",
            "allow": "Authorize",
        },
    )
    assert response.status_code == 302, getattr(response, "content", b"")
    code = parse_qs(urlparse(response["Location"]).query)["code"][0]
    access_token = _exchange_read_only(client, client_id=client_id, code=code, verifier=verifier)
    auth = {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}

    status, payload, response = _rpc(
        client,
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "p"}},
        **auth,
    )
    assert status == 200, payload
    session = {
        "HTTP_MCP_SESSION_ID": response["Mcp-Session-Id"],
        "HTTP_MCP_PROTOCOL_VERSION": PROTOCOL_VERSION,
        **auth,
    }

    status, payload, _ = _rpc(
        client,
        "tools/call",
        {"name": "issue_invoice", "arguments": {"number": "INV-2", "amount_cents": 1}},
        **session,
    )
    # Authenticated, so not a 401 — and the denial rides *inside* a 200 as a
    # JSON-RPC error, not as an HTTP status. Worth pinning: the docs claimed a
    # `403` with `scope=` in a `WWW-Authenticate` header, and no such path
    # exists — the only challenge-bearing response is the 401 above. What the
    # client actually gets is `requiredScopes`, which is the actionable part.
    assert status == 200, payload
    assert payload["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert payload["error"]["data"]["requiredScopes"] == ["mcp:write"]
    assert not Invoice.objects.filter(number="INV-2").exists()


def _exchange_read_only(client: Client, *, client_id: str, code: str, verifier: str) -> str:
    response = client.post(
        "/o/token/",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    assert body["scope"] == "mcp:read"
    return body["access_token"]
