from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType

from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.schema.agent_conventions import (
    HANDLE_DESCRIPTION,
    HANDLE_LINE,
    MISSING_ARGUMENTS,
    PAGED_QUERY_PARAM_SCOPE,
)


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

    **Every field is a ``str.format`` template**, rendered wherever it lands
    whether or not it has a placeholder, so a literal brace is written twice,
    ``{{`` or ``}}``, in every field alike. The placeholders a field accepts are
    the ones listed on it, and only ``missing_arguments`` accepts one. Anything
    the server could not render raises ``ImproperlyConfigured`` naming the field
    when the conventions are built, so a typo fails when the server is, rather
    than inside the first call that reaches it. This is the Pydantic-AI
    toolset's rule for its ``AgentConventions``, so one template reads the same
    on both transports.

    Some of these sentences state facts about how the server behaves: that a
    selection applies to each item and never to the envelope, that the
    envelope's keys are ``items``, ``page``, ``totalPages`` and ``hasNext``.
    Wording you supply owns keeping those facts true.

    Raises:
        ImproperlyConfigured: A field uses a placeholder it does not accept, is
            not a valid format string, cannot be rendered with the values it
            will be given (``{names:q}``), is neither a string nor ``None``, or
            is ``missing_arguments`` set to ``None`` or to an empty or
            whitespace-only string.
    """

    handle_field_description: str | None = HANDLE_DESCRIPTION
    """The ``outputSchema`` description of a handle field that declares none of
    its own, beside the field in every tool's output schema. ``None`` leaves
    such a field undescribed. A ``FieldMarking.handle("...")`` keeps its own
    words whatever this says. No placeholders."""

    handle_line: str | None = HANDLE_LINE
    """The line appended to the description of a tool whose output carries a
    handle, after ``Identify records by `<label>`.`` when the output serializer
    declares a label. ``None`` drops the line and that prefix with it. No
    placeholders."""

    query_param_on_pages: str | None = PAGED_QUERY_PARAM_SCOPE
    """What a read-shaping ``QueryParam`` applies to on a tool that returns a
    page. Appended to the param's description in the tool's ``inputSchema``, and
    to the ``isError`` message when a value it supplied is refused while that
    tool's result is rendered. ``None`` drops it from both. No placeholders."""

    missing_arguments: str = MISSING_ARGUMENTS
    """The message of a ``validation_error`` result for a call that left out
    arguments the tool's selectors cannot run without: a selector parameter
    with no default, a service tool's target lookup, a
    ``UrlKwarg(required=True)``. ``{names}`` is the missing names, sorted, each
    in backticks, joined with ``", "``; the ``detail`` stays keyed by each name.
    An input serializer's own refusal, a missing field included, keeps
    ``"Invalid arguments"``. Never ``None``, empty or only whitespace: it is
    the result's whole message."""

    def __post_init__(self) -> None:
        # Checked here, at construction, so a typo fails when the server is
        # built rather than inside the first tool call that renders it.
        for field in fields(self):
            _validate(field.name, getattr(self, field.name))


_PLACEHOLDERS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "handle_field_description": frozenset(),
        "handle_line": frozenset(),
        "query_param_on_pages": frozenset(),
        "missing_arguments": frozenset({"names"}),
    }
)
"""The placeholders each field accepts.

Keyed by every field, so a field added without an entry fails every
construction (``test_every_field_declares_its_placeholders``) rather than
accepting anything."""

_SAMPLES: Mapping[str, str] = MappingProxyType({"names": "`name`"})
"""A value of the type each placeholder is rendered with, for the trial render:
``{names}`` receives the missing names already joined into one string."""

_NEVER_NONE = frozenset({"missing_arguments"})
"""The fields that are the whole message of what they word, so can be neither
dropped nor left blank: an empty string would send the model exactly the empty
message the ``None`` refusal exists to stop."""


def _validate(name: str, value: object) -> None:
    """Refuse a field the server could not render, naming it.

    The checks the Pydantic-AI toolset makes of its own ``AgentConventions``, in
    the same order, so a template one transport accepts the other does too. The
    placeholder names are read with ``string.Formatter().parse`` and checked
    against the field's own set; then the template is rendered once with sample
    values of the types it will receive, which is what catches a conversion or a
    format spec those values cannot take (``{names:q}``, ``{names!z}``), and a
    field nested inside a format spec (``{names:>{width}}``), which the parse
    does not report. Each step is held by its own test in
    ``tests/schema/types/test_agent_conventions.py``, named at the step.
    """
    if value is None:
        # ``test_missing_arguments_has_no_none``; the bare ``return`` below it
        # by ``test_every_other_field_takes_none_or_an_empty_string[none-*]``.
        if name in _NEVER_NONE:
            raise ImproperlyConfigured(
                f"AgentConventions.{name} cannot be None: it is the whole message of a "
                "result, and a result with nothing in it tells the model nothing."
            )
        return
    # ``test_a_value_that_is_not_a_string_is_refused``. Served as given before,
    # so an ``int`` reached ``outputSchema`` as a number; ``string.Formatter``
    # would refuse it with a bare ``TypeError`` that names no field.
    if not isinstance(value, str):
        expected = "a string" if name in _NEVER_NONE else "a string or None"
        raise ImproperlyConfigured(
            f"AgentConventions.{name} must be {expected}, not {type(value).__name__}."
        )
    # One branch arc for two conditions, so each is named by the test that
    # fails without it: ``name in _NEVER_NONE`` by
    # ``test_every_other_field_takes_none_or_an_empty_string[empty-*]``, and
    # ``not value.strip()`` by every test there is, since without it the
    # default ``missing_arguments`` is refused and the default instance a
    # viewset holds fails at import.
    if name in _NEVER_NONE and not value.strip():
        raise ImproperlyConfigured(
            f"AgentConventions.{name} cannot be empty or only whitespace: it is the whole "
            "message of a result, and a result with nothing in it tells the model nothing."
        )
    accepted = _PLACEHOLDERS[name]
    # ``test_a_stray_brace_is_refused_in_every_field[unclosed-*]`` and
    # ``[unopened-*]``.
    try:
        used = {field for _, field, _, _ in string.Formatter().parse(value) if field is not None}
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"AgentConventions.{name} is not a valid format string ({exc}); write a literal "
            "brace twice, as {{ or }}."
        ) from exc
    # ``test_a_stray_brace_is_refused_in_every_field[stray-placeholder-*]`` and
    # ``test_a_bad_placeholder_fails_at_construction_naming_the_field``.
    unknown = sorted(used - accepted)
    if unknown:
        listed = ", ".join(f"{{{placeholder}}}" for placeholder in unknown)
        allowed = ", ".join(f"{{{placeholder}}}" for placeholder in sorted(accepted)) or "none"
        raise ImproperlyConfigured(
            f"AgentConventions.{name} uses {listed}, which it does not accept (accepted: "
            f"{allowed}); write a literal brace twice, as {{{{ or }}}}."
        )
    # ``test_a_template_its_values_cannot_render_is_refused_at_construction``.
    try:
        value.format(**{placeholder: _SAMPLES[placeholder] for placeholder in accepted})
    # Whatever the reason, a template that cannot be rendered with the values it
    # will be given is a configuration error, and this is the moment to say so:
    # the alternative is a ``-32603`` on the first call that reaches it.
    except Exception as exc:
        raise ImproperlyConfigured(f"AgentConventions.{name} cannot be rendered: {exc!r}.") from exc


__all__ = ["AgentConventions"]
