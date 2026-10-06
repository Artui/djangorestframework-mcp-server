from __future__ import annotations

import string
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.schema.agent_conventions import (
    HANDLE_DESCRIPTION,
    HANDLE_LINE,
    MISSING_ARGUMENTS,
    PAGED_QUERY_PARAM_SCOPE,
)

# The placeholders each formatted field may use. Only ``missing_arguments`` is
# formatted; every other field is written out as given, braces and all.
_PLACEHOLDERS: dict[str, frozenset[str]] = {"missing_arguments": frozenset({"names"})}


@dataclass(frozen=True)
class AgentConventions:
    """The sentences this server writes for a model, one field per sentence.

    Passed as ``MCPServer(conventions=...)``, per server, because two servers in
    one project can be talking to different readers. ``AgentConventions()`` is
    the default, and its fields carry the wording a server uses when it is given
    none; change one field and every other sentence stays the package's, so a
    later correction to a line you did not override still reaches you.

    The server still decides **whether** each sentence appears: a tool with no
    handle gets no handle line, a tool that returns no page no scope sentence.
    A field changes **what it says**, never when. ``None`` drops a sentence where
    dropping one leaves a well-formed answer.

    Some of these sentences state facts about how the server behaves: that a
    selection applies to each item and never to the envelope, that the
    envelope's keys are ``items``, ``page``, ``totalPages`` and ``hasNext``.
    Wording you supply owns keeping those facts true.
    """

    handle_field_description: str | None = HANDLE_DESCRIPTION
    """The ``outputSchema`` description of a handle field that declares none of
    its own, beside the field in every tool's output schema. ``None`` leaves
    such a field undescribed. A ``FieldMarking.handle("...")`` keeps its own
    words whatever this says."""

    handle_line: str | None = HANDLE_LINE
    """The line appended to the description of a tool whose output carries a
    handle, after ``Identify records by `<label>`.`` when the output serializer
    declares a label. ``None`` drops the line and that prefix with it."""

    query_param_on_pages: str | None = PAGED_QUERY_PARAM_SCOPE
    """What a read-shaping ``QueryParam`` applies to on a tool that returns a
    page. Appended to the param's description in the tool's ``inputSchema``, and
    to the ``isError`` message when a value it supplied is refused while that
    tool's result is rendered. ``None`` drops it from both."""

    missing_arguments: str = MISSING_ARGUMENTS
    """The message of a ``validation_error`` result for a call that left out
    arguments the tool's selectors cannot run without: a selector parameter
    with no default, a service tool's target lookup, a
    ``UrlKwarg(required=True)``. ``{names}`` is the missing names, sorted, each
    in backticks, joined with ``", "``; the ``detail`` stays keyed by each name.
    An input serializer's own refusal, a missing field included, keeps
    ``"Invalid arguments"``. Write a literal brace as ``{{`` or ``}}``."""

    def __post_init__(self) -> None:
        # Checked here, at construction, so a typo fails when the server is
        # built rather than inside the first tool call that formats it.
        for name, allowed in _PLACEHOLDERS.items():
            text: object = getattr(self, name)
            # A formatted field is the whole message of the result it words, so
            # it has no ``None`` to fall back on, and ``string.Formatter``
            # would refuse anything but a string with a bare ``TypeError``.
            if not isinstance(text, str):
                raise ImproperlyConfigured(
                    f"AgentConventions.{name} must be a string, not {type(text).__name__}."
                )
            try:
                used = {
                    field for _, field, _, _ in string.Formatter().parse(text) if field is not None
                }
            except ValueError as exc:
                raise ImproperlyConfigured(
                    f"AgentConventions.{name} is not a valid format string: {exc}."
                ) from exc
            unknown = sorted(used - allowed)
            if unknown:
                raise ImproperlyConfigured(
                    f"AgentConventions.{name} uses unknown placeholder(s) {unknown}; "
                    f"it may use {sorted(allowed)}."
                )


__all__ = ["AgentConventions"]
