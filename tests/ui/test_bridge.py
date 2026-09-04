"""``bridge.js`` — the view half of the MCP Apps postMessage handshake.

Driven for real, against a fake host and a fake DOM, by
``bridge_harness.mjs``. Asserting on the bridge's *source text* was the
tempting option and it is the wrong one: this file is the one piece of the
package that runs in someone else's browser, every one of its failure modes is
silent, and a string match would agree with a broken bridge as readily as a
working one.

The harness runs each scenario and prints one JSON object. Node supplies the
engine; the DOM it fakes is only as wide as the handful of APIs the bridge
touches, which covers the whole protocol and nothing else.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

import rest_framework_mcp

BRIDGE = pathlib.Path(rest_framework_mcp.__file__).parent / "ui" / "bridge.js"
HARNESS = pathlib.Path(__file__).parent / "bridge_harness.mjs"

INITIALIZED = "ui/notifications/initialized"


@pytest.fixture(scope="module")
def scenarios() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        # Skipped locally, never in CI: a suite that silently stops running its
        # only behavioural test of the bridge is worth less than one that fails.
        if os.environ.get("CI"):
            pytest.fail("node is required to exercise bridge.js and is missing on this runner")
        pytest.skip("node is not installed; bridge.js behaviour is unverified here")
    completed = subprocess.run(
        [node, str(HARNESS), str(BRIDGE)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


class TestTheHandshakeAlwaysCompletes:
    """The reason this file exists.

    A host reveals the view's frame only once it receives ``initialized``, and
    the spec says nothing at all about what a view should do when
    ``ui/initialize`` comes back an error. The natural shape — treat the error
    as fatal, write an explanation into the document, return — never sends
    ``initialized``, so the frame stays hidden and the explanation is sealed
    inside it. Nothing throws, nothing is logged, and the server sees a clean
    ``resources/read``.

    That is not a hypothetical: it is what a consumer hit, and it is what the
    extension's own SDK still does in ``App.connect``, which awaits the
    initialize request inside a ``try`` and jumps past the notification on
    rejection.
    """

    def test_on_a_successful_reply(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["success"]["methods"]

    def test_on_an_error_reply(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["error_reply"]["methods"]

    def test_on_no_reply_at_all(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["no_reply"]["methods"]

    def test_it_is_sent_exactly_once_when_a_late_reply_follows_the_timeout(
        self, scenarios: dict[str, Any]
    ) -> None:
        """The timeout and the reply are two paths to the same funnel, and a
        host that receives ``initialized`` twice is entitled to complain."""
        assert scenarios["timeout_then_reply"]["methods"].count(INITIALIZED) == 1

    def test_a_failed_handshake_leaves_a_visible_explanation(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Completing the handshake is what buys the diagnostic surface: the
        frame is revealed, so there is somewhere for this text to appear."""
        assert "host said no" in " ".join(scenarios["error_reply"]["banner"])


class TestTheInitializeRequest:
    def test_it_sends_the_field_names_the_schema_requires(self, scenarios: dict[str, Any]) -> None:
        """``appInfo`` and ``appCapabilities``, not ``clientInfo`` and
        ``capabilities``. The spec's own prose and its worked example disagree
        with each other here — the pinned ``2026-01-26`` revision's example
        sends ``capabilities`` against normative text requiring
        ``appCapabilities`` — so this follows the generated schema, which is
        what hosts are built from."""
        params = scenarios["success"]["initialize_params"]
        assert set(params) == {"appInfo", "appCapabilities", "protocolVersion"}
        assert params["protocolVersion"] == "2026-01-26"


class TestHostContext:
    def test_the_theme_locale_and_style_variables_are_applied(
        self, scenarios: dict[str, Any]
    ) -> None:
        result = scenarios["success"]
        assert result["theme"] == "dark"
        assert result["lang"] == "fr-FR"
        assert result["css_variables"] == {"--text-primary": "#eee"}

    def test_the_negotiated_protocol_version_is_kept(self, scenarios: dict[str, Any]) -> None:
        assert scenarios["success"]["protocol_version"] == "2026-01-26"


class TestDataDelivery:
    def test_handlers_assigned_during_parsing_receive_input_and_results(
        self, scenarios: dict[str, Any]
    ) -> None:
        """The bridge loads from ``<head>`` and connects on ``DOMContentLoaded``
        precisely so a fragment's inline script gets to register first."""
        seen = scenarios["tool_result"]["seen"]
        assert seen[0] == ["input", {"ordering": "-total"}]
        assert seen[1][0] == {"results": [{"id": 1}]}

    def test_a_throwing_handler_does_not_take_the_bridge_down(
        self, scenarios: dict[str, Any]
    ) -> None:
        """A view whose render function raises still has to be able to say so,
        and still has to keep reporting its size."""
        result = scenarios["handler_throws"]
        assert "render blew up" in " ".join(result["banner"])
        assert "ui/notifications/size-changed" in result["methods"]


class TestRequestsFromTheHost:
    def test_an_unimplemented_method_is_answered_rather_than_ignored(
        self, scenarios: dict[str, Any]
    ) -> None:
        """An unanswered request leaves the host waiting on a promise that
        never settles — the same silence, from the other direction."""
        answers = {a["id"]: a for a in scenarios["unknown_request"]["answers"]}
        assert answers[99]["error"]["code"] == -32601

    def test_teardown_is_acknowledged(self, scenarios: dict[str, Any]) -> None:
        answers = {a["id"]: a for a in scenarios["unknown_request"]["answers"]}
        assert answers[100]["result"] == {}


class TestOutsideAHost:
    def test_a_directly_opened_document_says_so_and_posts_nothing(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Opening the view in a browser is how a project develops one. Posting
        to our own window would have the document receive its own messages."""
        result = scenarios["unframed"]
        assert result["posted"] == 0
        assert "rendering outside a host" in " ".join(result["banner"])


class TestCallingBack:
    def test_call_tool_goes_out_as_an_ordinary_tools_call_request(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Which is what makes the server's permissions and rate limits apply
        to it unchanged."""
        result = scenarios["call_tool"]
        assert result["method"] == "tools/call"
        assert result["params"] == {"name": "list_invoices", "arguments": {"ordering": "-total"}}
        assert result["has_id"] is True
