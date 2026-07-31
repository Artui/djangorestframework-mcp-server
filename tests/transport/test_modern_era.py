"""The 2026-07-28 transport: era detection, header validation, status codes.

Every request here goes through the real URL conf, because the whole subject is
what happens at the HTTP edge — which status code, which headers, whether a
session is minted. Asserting on handler return values would test none of it.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from django.test import AsyncClient, Client, override_settings

MODERN = "2026-07-28"
LEGACY = "2025-11-25"

pytestmark = pytest.mark.urls("tests.conformance.urls")


def _meta(version: str = MODERN) -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "TestClient", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _post(
    client: Client,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    version: str = MODERN,
    headers: dict[str, str] | None = None,
    include_meta: bool = True,
) -> Any:
    body_params: dict[str, Any] = dict(params or {})
    if include_meta:
        body_params["_meta"] = _meta(version)
    sent: dict[str, str] = {
        "Mcp-Protocol-Version": version,
        "Mcp-Method": method,
        **(headers or {}),
    }
    return client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": body_params}),
        content_type="application/json",
        headers={k: v for k, v in sent.items() if v is not None},
    )


# ----- era detection -----


@pytest.mark.django_db(transaction=True)
def test_a_modern_request_needs_no_session(client: Client) -> None:
    """No handshake, no session — that is the entire point of the revision."""
    response = _post(client, "tools/list")
    assert response.status_code == 200, response.content
    assert "tools" in response.json()["result"]


@pytest.mark.django_db(transaction=True)
def test_a_modern_request_mints_no_session(client: Client) -> None:
    response = _post(client, "tools/list")
    assert "Mcp-Session-Id" not in response.headers


@pytest.mark.django_db(transaction=True)
def test_a_session_header_on_a_modern_request_is_ignored(client: Client) -> None:
    """The spec asks a modern server to ignore one rather than reject it."""
    response = _post(client, "tools/list", headers={"Mcp-Session-Id": "whatever"})
    assert response.status_code == 200, response.content


@pytest.mark.django_db(transaction=True)
def test_the_legacy_path_is_untouched(client: Client, initialized_session: str) -> None:
    """A request without modern ``_meta`` still needs its session."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": LEGACY, "Mcp-Session-Id": initialized_session},
    )
    assert response.status_code == 200, response.content


@pytest.mark.django_db(transaction=True)
def test_initialize_never_offers_a_modern_version(client: Client) -> None:
    """``initialize`` does not exist in 2026-07-28, so offering it would hand
    the client a revision whose next request this transport would refuse."""
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["result"]["protocolVersion"] == LEGACY


# ----- header validation -----


@pytest.mark.django_db(transaction=True)
def test_a_version_header_that_contradicts_the_body_is_rejected(client: Client) -> None:
    response = _post(client, "tools/list", headers={"Mcp-Protocol-Version": LEGACY})
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == -32020


@pytest.mark.django_db(transaction=True)
def test_a_missing_method_header_is_rejected(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _meta()}}
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": MODERN},
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == -32020


@pytest.mark.django_db(transaction=True)
def test_a_method_header_that_contradicts_the_body_is_rejected(client: Client) -> None:
    """The confused-deputy case: a gateway routes on the header, the server
    executes the body, and they disagree about what is being called."""
    response = _post(client, "tools/list", headers={"Mcp-Method": "tools/call"})
    assert response.status_code == 400, response.content
    assert "Mcp-Method" in response.json()["error"]["message"]


@pytest.mark.django_db(transaction=True)
def test_the_name_header_is_required_for_tools_call(client: Client) -> None:
    response = _post(client, "tools/call", {"name": "conformance.bulk_listed", "arguments": {}})
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == -32020


@pytest.mark.django_db(transaction=True)
def test_a_matching_name_header_passes(client: Client) -> None:
    response = _post(
        client,
        "tools/call",
        {"name": "conformance.bulk_listed", "arguments": {}},
        headers={"Mcp-Name": "conformance.bulk_listed"},
    )
    assert response.status_code == 200, response.content


@pytest.mark.django_db(transaction=True)
def test_a_base64_sentinel_name_is_decoded_before_comparison(client: Client) -> None:
    """A name that will not survive as a plain ASCII header rides encoded."""
    encoded = base64.b64encode(b"conformance.bulk_listed").decode("ascii")
    response = _post(
        client,
        "tools/call",
        {"name": "conformance.bulk_listed", "arguments": {}},
        headers={"Mcp-Name": f"=?base64?{encoded}?="},
    )
    assert response.status_code == 200, response.content


@pytest.mark.django_db(transaction=True)
def test_a_malformed_base64_sentinel_is_rejected(client: Client) -> None:
    response = _post(
        client,
        "tools/call",
        {"name": "conformance.bulk_listed", "arguments": {}},
        headers={"Mcp-Name": "=?base64?not-base64!?="},
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == -32020


@pytest.mark.django_db(transaction=True)
def test_the_name_header_is_compared_for_resources_read(client: Client) -> None:
    response = _post(
        client,
        "resources/read",
        {"uri": "conformance://nothing"},
        headers={"Mcp-Name": "conformance://something-else"},
    )
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == -32020


@pytest.mark.django_db(transaction=True)
def test_a_method_with_no_name_source_needs_no_name_header(client: Client) -> None:
    assert _post(client, "prompts/list").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_a_missing_body_name_is_left_to_the_handler(client: Client) -> None:
    """Header validation stands aside for a params fault it cannot describe."""
    response = _post(client, "tools/call", {"arguments": {}})
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32602


# ----- status codes -----


@pytest.mark.django_db(transaction=True)
def test_an_unsupported_version_lists_what_is_supported(client: Client) -> None:
    response = _post(client, "tools/list", version="1900-01-01")
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == -32022
    assert error["data"]["requested"] == "1900-01-01"
    assert error["data"]["supported"] == [MODERN]


@pytest.mark.django_db(transaction=True)
def test_a_legacy_version_claimed_by_a_modern_request_is_unsupported(client: Client) -> None:
    """Modern ``_meta`` naming a legacy revision is not a legacy request — it
    is a modern one asking for a version the modern path does not serve."""
    response = _post(client, "tools/list", version=LEGACY)
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == -32022


@pytest.mark.django_db(transaction=True)
def test_an_unknown_method_is_404_with_a_jsonrpc_body(client: Client) -> None:
    """The 404 is what lets a client tell this endpoint from a legacy HTTP+SSE
    server; the JSON-RPC body is what stops it falling back anyway."""
    response = _post(client, "nonsense/method")
    assert response.status_code == 404, response.content
    assert response.json()["error"]["code"] == -32601


@pytest.mark.django_db(transaction=True)
def test_a_permission_denial_is_still_403(client: Client) -> None:
    response = _post(
        client,
        "tools/call",
        {"name": "conformance.gated", "arguments": {}},
        headers={"Mcp-Name": "conformance.gated"},
    )
    assert response.status_code == 403, response.content


@pytest.mark.django_db(transaction=True)
def test_resource_not_found_uses_the_modern_code(client: Client) -> None:
    """``-32002`` is the legacy answer; the modern revision replaced it."""
    response = _post(
        client,
        "resources/read",
        {"uri": "conformance://nothing"},
        headers={"Mcp-Name": "conformance://nothing"},
    )
    assert response.status_code == 200, response.content
    assert response.json()["error"]["code"] == -32602


@pytest.mark.django_db(transaction=True)
def test_resource_not_found_stays_32002_for_a_legacy_caller(
    client: Client, initialized_session: str
) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "conformance://nothing"},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": LEGACY, "Mcp-Session-Id": initialized_session},
    )
    assert response.json()["error"]["code"] == -32002


@pytest.mark.django_db(transaction=True)
def test_a_modern_notification_is_202(client: Client) -> None:
    response = client.post(
        "/mcp/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {"_meta": _meta()},
            }
        ),
        content_type="application/json",
        headers={"Mcp-Protocol-Version": MODERN},
    )
    assert response.status_code == 202


# ----- retired mechanisms -----


@pytest.mark.django_db(transaction=True)
def test_delete_is_405_for_a_modern_caller(client: Client) -> None:
    """Session termination does not exist in the revision the caller named."""
    response = client.delete("/mcp/", headers={"Mcp-Protocol-Version": MODERN})
    assert response.status_code == 405


@pytest.mark.django_db(transaction=True)
def test_delete_still_works_for_a_legacy_caller(client: Client) -> None:
    response = client.delete("/mcp/", headers={"Mcp-Protocol-Version": LEGACY})
    assert response.status_code == 204


@pytest.mark.django_db(transaction=True)
async def test_the_async_get_stream_is_405_for_a_modern_caller() -> None:
    """The standalone GET stream was removed in 2026-07-28.

    Mounts the async URL conf directly rather than through the module-level
    ``urls`` marker, which this file points at the sync transport.
    """
    with override_settings(ROOT_URLCONF="tests.testapp.async_urls"):
        response = await AsyncClient().get("/mcp/", headers={"Mcp-Protocol-Version": MODERN})
    assert response.status_code == 405


@pytest.mark.django_db(transaction=True)
async def test_the_async_modern_path_matches_the_sync_one() -> None:
    """The two transports are parallel implementations, so parity is asserted
    rather than assumed — that is exactly where they have drifted before."""
    with override_settings(ROOT_URLCONF="tests.testapp.async_urls"):
        client = AsyncClient()
        ok = await client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {"_meta": _meta()},
                }
            ),
            content_type="application/json",
            headers={"Mcp-Protocol-Version": MODERN, "Mcp-Method": "tools/list"},
        )
        mismatch = await client.post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {"_meta": _meta()},
                }
            ),
            content_type="application/json",
            headers={"Mcp-Protocol-Version": MODERN, "Mcp-Method": "tools/call"},
        )
    assert ok.status_code == 200, ok.content
    assert "Mcp-Session-Id" not in ok.headers
    assert mismatch.status_code == 400
    assert json.loads(mismatch.content)["error"]["code"] == -32020


# ----- gaps the happy path does not reach -----


@pytest.mark.django_db(transaction=True)
async def test_a_modern_permission_denial_is_403_with_a_challenge_async() -> None:
    """The status and the challenge survive the era change — a client still
    learns what to ask for rather than retrying the same token."""
    from tests.conformance.mcp import build_conformance_server
    from tests.testapp.urlconf_for import urlconf_for

    conf = urlconf_for(build_conformance_server(), is_async=True)
    with override_settings(ROOT_URLCONF=conf):
        response = await AsyncClient().post(
            "/mcp/",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "conformance.gated",
                        "arguments": {},
                        "_meta": _meta(),
                    },
                }
            ),
            content_type="application/json",
            headers={
                "Mcp-Protocol-Version": MODERN,
                "Mcp-Method": "tools/call",
                "Mcp-Name": "conformance.gated",
            },
        )
    assert response.status_code == 403, response.content
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


def test_a_broken_meta_reads_as_legacy() -> None:
    """A missing modern marker and a malformed one are indistinguishable here.

    Answering a legacy client with a modern header-validation error would be
    the more confusing of the two failures, so ``from_params`` degrades to
    "legacy" and lets the legacy path have its say.
    """
    from rest_framework_mcp.transport.types.request_metadata import RequestMetadata

    assert RequestMetadata.from_params(None) is None
    assert RequestMetadata.from_params({"_meta": "not-a-mapping"}) is None
    assert RequestMetadata.from_params({"_meta": {}}) is None
    assert (
        RequestMetadata.from_params({"_meta": {"io.modelcontextprotocol/protocolVersion": 2026}})
        is None
    )
    assert (
        RequestMetadata.from_params({"_meta": {"io.modelcontextprotocol/protocolVersion": ""}})
        is None
    )


def test_client_info_is_projected_as_far_as_it_goes() -> None:
    """Self-reported and unverified, so a half-filled one is not rejected."""
    from rest_framework_mcp.transport.types.request_metadata import RequestMetadata

    partial = RequestMetadata.from_params(
        {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientInfo": {"name": "C"},
                "io.modelcontextprotocol/clientCapabilities": "not-a-mapping",
            }
        }
    )
    assert partial is not None
    assert partial.client_info is not None
    assert partial.client_info.name == "C"
    assert partial.client_info.version == ""
    # A non-mapping capabilities declaration degrades to "declared nothing".
    assert partial.client_capabilities == {}

    nameless = RequestMetadata.from_params(
        {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientInfo": {"version": "1.0"},
            }
        }
    )
    assert nameless is not None and nameless.client_info is None
