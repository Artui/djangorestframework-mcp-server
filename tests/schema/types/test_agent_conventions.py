"""``AgentConventions`` -- the wording this transport writes for a model, as values.

The defaults are pinned here as literals, not against the constants they are
built from: a default that drifts is a wording change nobody reviewed, and a
test comparing a field to the constant it reads would pass through any edit of
that constant.
"""

from __future__ import annotations

import dataclasses
from dataclasses import fields

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp import AgentConventions
from rest_framework_mcp.schema import HANDLE_DESCRIPTION, PAGED_QUERY_PARAM_SCOPE
from rest_framework_mcp.schema.types import agent_conventions

_FIELDS = [field.name for field in fields(AgentConventions)]


def test_the_handle_description_is_the_toolsets_sentence() -> None:
    # Changed on purpose: it was "Opaque identifier. Pass it to other tools
    # that ask for one; do not read it out." This is the Pydantic-AI toolset's
    # sentence, so one spec reads the same on both routes.
    expected = (
        "An opaque identifier. Pass it to other tools that ask for one; refer to the "
        "record by its name in anything you say, never by this value."
    )

    assert AgentConventions().handle_field_description == expected
    assert expected == HANDLE_DESCRIPTION


def test_the_handle_line_gives_the_toolsets_advice_in_this_transports_framing() -> None:
    # Changed on purpose, with the description above: it ended "pass them on
    # where a tool asks for one, and never read them out."
    assert AgentConventions().handle_line == (
        "Fields described as opaque identifiers are for other tool calls, not for the "
        "reader: pass them to other tools that ask for one; refer to records by their "
        "name in anything you say, never by the identifier."
    )


def test_the_paged_scope_sentence_is_unchanged_byte_for_byte() -> None:
    expected = (
        "On a paged result it applies to each item in `items`, never to the page "
        "envelope (`items`, `page`, `totalPages`, `hasNext`)."
    )

    assert AgentConventions().query_param_on_pages == expected
    assert expected == PAGED_QUERY_PARAM_SCOPE


def test_the_missing_arguments_message_is_the_toolsets() -> None:
    assert AgentConventions().missing_arguments == "Missing required argument(s): {names}."


def test_conventions_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        AgentConventions().handle_line = "x"  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(
    "text",
    [
        "Left out: {names}.",
        # No placeholder at all is a fixed sentence, which is a choice.
        "A required argument is missing.",
        # Doubled braces are literal braces, as ``str.format`` reads them.
        "Missing {{required}}: {names}.",
    ],
)
def test_missing_arguments_accepts_its_placeholder_or_none(text: str) -> None:
    assert AgentConventions(missing_arguments=text).missing_arguments == text


@pytest.mark.parametrize(
    "text",
    [
        "Missing: {name}.",  # a typo of the one placeholder
        "Missing: {}.",  # positional
        "Missing: {names.upper}.",  # an attribute of it
        "Missing: {names",  # malformed
    ],
)
def test_a_bad_placeholder_fails_at_construction_naming_the_field(text: str) -> None:
    # At startup rather than inside a tool call, where it would surface as an
    # internal error on the first call that leaves an argument out.
    with pytest.raises(ImproperlyConfigured, match="missing_arguments"):
        AgentConventions(missing_arguments=text)


def test_every_field_declares_its_placeholders() -> None:
    # A field with no entry fails every construction rather than accepting
    # anything, so this is the reminder to give a new field one.
    assert dict(agent_conventions._PLACEHOLDERS) == {
        "handle_field_description": frozenset(),
        "handle_line": frozenset(),
        "query_param_on_pages": frozenset(),
        "missing_arguments": frozenset({"names"}),
    }
    assert set(agent_conventions._PLACEHOLDERS) == set(_FIELDS)


def test_every_default_renders_as_itself() -> None:
    # Every field is rendered before a model reads it, so a default carrying a
    # brace would change on the way out. None does: each renders byte for byte,
    # its placeholder filled with itself.
    defaults = AgentConventions()

    for name in _FIELDS:
        text = getattr(defaults, name)
        accepted = agent_conventions._PLACEHOLDERS[name]
        assert text.format(**{placeholder: f"{{{placeholder}}}" for placeholder in accepted}) == (
            text
        )


def test_missing_arguments_has_no_none() -> None:
    # The other fields drop a sentence on ``None``; this one is a result's
    # whole message, so there is nothing to drop it to.
    with pytest.raises(
        ImproperlyConfigured, match=r"AgentConventions\.missing_arguments cannot be None"
    ):
        AgentConventions(missing_arguments=None)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("value", ["", "   ", "\n\t "], ids=["empty", "spaces", "whitespace"])
def test_missing_arguments_cannot_be_blank_either(value: str) -> None:
    # ``""`` would send the model the same empty message the ``None`` refusal
    # exists to stop.
    with pytest.raises(ImproperlyConfigured) as refused:
        AgentConventions(missing_arguments=value)

    assert str(refused.value).startswith(
        "AgentConventions.missing_arguments cannot be empty or only whitespace"
    )


@pytest.mark.parametrize("name", [name for name in _FIELDS if name != "missing_arguments"])
@pytest.mark.parametrize("value", [None, ""], ids=["none", "empty"])
def test_every_other_field_takes_none_or_an_empty_string(name: str, value: str | None) -> None:
    # Blank is refused only where ``None`` is: elsewhere ``""`` is an empty
    # sentence, which is the caller's choice and not an error.
    assert getattr(AgentConventions(**{name: value}), name) == value


@pytest.mark.parametrize(
    ("name", "value", "says"),
    [
        ("handle_field_description", 7, "must be a string or None, not int"),
        ("handle_line", 123, "must be a string or None, not int"),
        ("query_param_on_pages", ["x"], "must be a string or None, not list"),
        ("missing_arguments", 7, "must be a string, not int"),
    ],
)
def test_a_value_that_is_not_a_string_is_refused(name: str, value: object, says: str) -> None:
    # Served as given before: an ``int`` description reached ``outputSchema``
    # as a number, and a list was joined into a message as its ``repr``.
    with pytest.raises(ImproperlyConfigured) as refused:
        AgentConventions(**{name: value})

    assert str(refused.value) == f"AgentConventions.{name} {says}."


@pytest.mark.parametrize("name", _FIELDS)
@pytest.mark.parametrize(
    ("template", "says"),
    [
        ("Ids {x} here.", "uses {x}, which it does not accept"),
        ("An id looks like {this.", "is not a valid format string"),
        ("Closed }", "is not a valid format string"),
    ],
    ids=["stray-placeholder", "unclosed", "unopened"],
)
def test_a_stray_brace_is_refused_in_every_field(name: str, template: str, says: str) -> None:
    # Every field is a ``str.format`` template, so a single brace is either a
    # placeholder or a mistake, in all of them alike.
    with pytest.raises(ImproperlyConfigured) as refused:
        AgentConventions(**{name: template})

    assert str(refused.value).startswith(f"AgentConventions.{name} {says}")


@pytest.mark.parametrize(
    "template",
    ["Missing {names:q}.", "{names!z}", "{names:>{width}}"],
    ids=["format-spec", "conversion", "nested-field"],
)
def test_a_template_its_values_cannot_render_is_refused_at_construction(template: str) -> None:
    # Each names only ``{names}``, which the field accepts, so the placeholder
    # check passes it and the trial render is what refuses: the negative
    # assertion says the earlier check did not answer first. Before, each was
    # built, and the first call leaving an argument out was a ``-32603``
    # over HTTP and a ``ValueError`` or ``KeyError`` from ``call_tool``.
    with pytest.raises(ImproperlyConfigured) as refused:
        AgentConventions(missing_arguments=template)

    message = str(refused.value)
    assert message.startswith("AgentConventions.missing_arguments cannot be rendered: ")
    assert "which it does not accept" not in message


@pytest.mark.parametrize("name", _FIELDS)
def test_a_doubled_brace_is_a_literal_and_not_a_placeholder(name: str) -> None:
    # Kept as written: the server renders it, which is where ``{{`` becomes
    # one brace (``test_a_doubled_brace_reaches_the_model_as_one_in_every_field``).
    template = "Ids look like {{this}}."

    assert getattr(AgentConventions(**{name: template}), name) == template
