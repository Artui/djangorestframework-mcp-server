"""``register_ui_resource`` — declaring an interactive HTML view."""

from __future__ import annotations

from typing import Any

import pytest
from django.template import TemplateDoesNotExist
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import (
    UI_META_KEY,
    UI_RESOURCE_MIME_TYPE,
    ResourceEncoding,
    UIPermission,
)
from rest_framework_mcp.registry.types.ui_csp import UICsp
from rest_framework_mcp.registry.types.ui_resource_meta import UIResourceMeta
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _make() -> MCPServer:
    return MCPServer(
        name="test",
        description="d",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )


def _register(server: MCPServer, **overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "name": "invoices_table",
        "uri": "ui://invoices/table.html",
        "html": "<h1>Invoices</h1>",
    }
    return server.register_ui_resource(**{**defaults, **overrides})


class _AlwaysAllow:
    """A minimal real permission — the sentinel it replaces gated nothing."""

    def has_permission(self, request: Any, token: Any) -> bool:
        return True


class TestDefaults:
    def test_advertises_the_apps_mime_type(self) -> None:
        assert _register(_make()).mime_type == UI_RESOURCE_MIME_TYPE

    def test_encodes_the_body_as_text(self) -> None:
        """HTML under JSON encoding comes back as a quoted string literal."""
        assert _register(_make()).encoding is ResourceEncoding.TEXT

    def test_is_a_retrieve(self) -> None:
        """A view is one document, not a collection."""
        assert _register(_make()).kind is SelectorKind.RETRIEVE

    def test_is_unguarded_by_default(self) -> None:
        """A view is a static asset on an already-authenticated session, and
        hosts may prefetch it before any tool call."""
        assert _register(_make()).permissions == ()

    def test_can_still_be_guarded(self) -> None:
        sentinel = _AlwaysAllow()
        assert _register(_make(), permissions=[sentinel]).permissions == (sentinel,)

    def test_lands_in_the_shared_resource_registry(self) -> None:
        server = _make()
        binding = _register(server)

        assert server.resources.resolve("ui://invoices/table.html") == (binding, {})

    def test_collides_with_a_data_resource_on_the_same_uri(self) -> None:
        """One URI namespace — a view is an ordinary resource."""
        server = _make()
        _register(server)

        with pytest.raises(ValueError, match="Duplicate MCP resource URI"):
            _register(server, name="other")


class TestContentSources:
    def test_html_is_returned_verbatim(self) -> None:
        binding = _register(_make(), html="<h1>Hi</h1>")
        assert binding.selector() == "<h1>Hi</h1>"

    def test_a_callable_supplies_the_document(self) -> None:
        binding = _register(_make(), html=None, selector=lambda: "<p>built</p>")
        assert binding.selector() == "<p>built</p>"

    def test_a_template_is_rendered(self) -> None:
        binding = _register(_make(), html=None, template_name="mcp_ui/view.html")
        assert "<h1>A view</h1>" in binding.selector()

    def test_a_template_is_rendered_per_read_not_at_registration(self) -> None:
        """So a template edit shows up without a restart, like every other
        Django template. Registration therefore does not touch the loader."""
        binding = _register(_make(), html=None, template_name="mcp_ui/does_not_exist.html")

        with pytest.raises(TemplateDoesNotExist):
            binding.selector()

    def test_no_content_source_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one content source"):
            _register(_make(), html=None)

    def test_two_content_sources_raise(self) -> None:
        with pytest.raises(ValueError, match="exactly one content source"):
            _register(_make(), html="<p>x</p>", selector=lambda: "<p>y</p>")


class TestUIMetadata:
    def test_no_ui_metadata_leaves_meta_empty(self) -> None:
        assert _register(_make()).meta == {}

    def test_serialises_under_the_extension_key(self) -> None:
        binding = _register(_make(), ui=UIResourceMeta(domain="example.com"))
        assert binding.meta == {UI_META_KEY: {"domain": "example.com"}}

    def test_csp_uses_the_camel_case_wire_names(self) -> None:
        binding = _register(
            _make(),
            ui=UIResourceMeta(
                csp=UICsp(
                    connect_domains=("https://api.example.com",),
                    resource_domains=("https://cdn.example.com",),
                    frame_domains=("https://embed.example.com",),
                    base_uri_domains=("https://example.com",),
                )
            ),
        )

        assert binding.meta[UI_META_KEY]["csp"] == {
            "connectDomains": ["https://api.example.com"],
            "resourceDomains": ["https://cdn.example.com"],
            "frameDomains": ["https://embed.example.com"],
            "baseUriDomains": ["https://example.com"],
        }

    def test_an_empty_csp_is_omitted_rather_than_sent_as_an_empty_object(self) -> None:
        binding = _register(_make(), ui=UIResourceMeta(csp=UICsp(), prefers_border=True))
        assert binding.meta[UI_META_KEY] == {"prefersBorder": True}

    def test_permissions_serialise_as_their_wire_values(self) -> None:
        binding = _register(
            _make(),
            ui=UIResourceMeta(permissions=(UIPermission.CAMERA, UIPermission.CLIPBOARD_WRITE)),
        )

        assert binding.meta[UI_META_KEY]["permissions"] == ["camera", "clipboardWrite"]

    def test_prefers_border_false_is_declared_not_dropped(self) -> None:
        """``False`` is a real preference; only ``None`` means "didn't say"."""
        binding = _register(_make(), ui=UIResourceMeta(prefers_border=False))
        assert binding.meta[UI_META_KEY] == {"prefersBorder": False}

    def test_other_extensions_keep_their_own_keys(self) -> None:
        binding = _register(
            _make(),
            ui=UIResourceMeta(domain="example.com"),
            meta={"example.com/other": {"k": 1}},
        )

        assert set(binding.meta) == {UI_META_KEY, "example.com/other"}

    def test_declaring_the_ui_key_twice_raises(self) -> None:
        """Both write the same ``_meta`` key, so one would silently win — and
        the symptom is a view that never renders."""
        with pytest.raises(ValueError, match="both ui= and a 'ui' key"):
            _register(
                _make(),
                ui=UIResourceMeta(domain="example.com"),
                meta={UI_META_KEY: {"domain": "other.example"}},
            )

    def test_a_hand_written_ui_key_alone_is_allowed(self) -> None:
        """The escape hatch stays open for a key the typed shape can't express."""
        binding = _register(_make(), meta={UI_META_KEY: {"future": True}})
        assert binding.meta == {UI_META_KEY: {"future": True}}


class TestAViewSelectorCannotReadTheCaller:
    """The premise the permissions exemption rests on, made true by construction.

    ``register_ui_resource`` is the one registration on this server that skips
    ``check_tool_permissions_declared``, and the reason given is that a view's
    content cannot depend on who is asking. Two of the three content sources
    make that true on their own. The third did not: ``selector=`` was documented
    and typed as zero-argument, nothing enforced it, and
    ``handle_resources_read`` resolves every binding's selector by name against a
    pool that deliberately carries ``request`` and ``user``.

    So ``selector=lambda user: ...`` was handed the authenticated caller, was
    registered unguarded because of the exemption, and produced a document a host
    may cache across callers. Each of those is defensible alone.
    """

    def test_a_selector_naming_the_user_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must take no arguments"):
            _register(
                _make(),
                html=None,
                selector=lambda user: f"<h1>{user}</h1>",  # ty: ignore[invalid-argument-type]
            )

    def test_a_selector_naming_the_request_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must take no arguments"):
            _register(
                _make(),
                html=None,
                selector=lambda request: "<h1>hi</h1>",  # ty: ignore[invalid-argument-type]
            )

    def test_a_selector_taking_anything_at_all_is_refused(self) -> None:
        # The pool is not a fixed set -- URI-template variables land in it too --
        # so the refusal is about declaring a fillable parameter, not about the
        # two names that happen to be the dangerous ones today.
        with pytest.raises(ValueError, match="must take no arguments"):
            _register(
                _make(),
                html=None,
                selector=lambda anything: "<h1>hi</h1>",  # ty: ignore[invalid-argument-type]
            )

    def test_a_selector_with_a_default_is_still_refused(self) -> None:
        # A default does not stop resolve_callable_kwargs filling the parameter
        # when the name is in the pool; it only hides the failure if it is not.
        with pytest.raises(ValueError, match="must take no arguments"):
            _register(
                _make(),
                html=None,
                selector=lambda user=None: "<h1>hi</h1>",  # ty: ignore[invalid-argument-type]
            )

    def test_a_var_keyword_selector_is_refused(self) -> None:
        # The worst case rather than an edge one: resolve_callable_kwargs hands
        # the *entire* pool to a callable declaring **kwargs.
        def selector(**kwargs: object) -> str:
            return "<h1>hi</h1>"

        with pytest.raises(ValueError, match="must take no arguments"):
            _register(_make(), html=None, selector=selector)

    def test_the_message_names_the_remedy(self) -> None:
        with pytest.raises(ValueError) as caught:
            _register(
                _make(),
                html=None,
                selector=lambda user: "<h1>hi</h1>",  # ty: ignore[invalid-argument-type]
            )
        message = str(caught.value)
        # A registration-time refusal is only useful if it says what to do next.
        assert "register_resource" in message
        assert "'user'" in message

    def test_a_zero_argument_selector_is_accepted(self) -> None:
        binding = _register(_make(), html=None, selector=lambda: "<h1>hi</h1>")

        assert binding.selector() == "<h1>hi</h1>"

    def test_a_variadic_positional_selector_is_accepted(self) -> None:
        # resolve_callable_kwargs only ever builds keyword arguments, so this
        # one is still called with nothing.
        def selector(*args: object) -> str:
            return "<h1>hi</h1>"

        binding = _register(_make(), html=None, selector=selector)

        assert binding.selector() == "<h1>hi</h1>"

    def test_the_other_two_sources_are_untouched(self) -> None:
        # The exemption was always sound for these; the fix must not narrow them.
        assert _register(_make(), html="<p>a</p>").selector() == "<p>a</p>"
        assert _register(_make(), template_name=None, html="<p>b</p>").selector() == "<p>b</p>"
