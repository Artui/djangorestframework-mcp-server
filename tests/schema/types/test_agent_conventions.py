"""``AgentConventions`` -- the wording this transport writes for a model, as values.

The defaults are pinned here as literals, not against the constants they are
built from: a default that drifts is a wording change nobody reviewed, and a
test comparing a field to the constant it reads would pass through any edit of
that constant.
"""

from __future__ import annotations

import dataclasses

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp import AgentConventions
from rest_framework_mcp.schema import HANDLE_DESCRIPTION, PAGED_QUERY_PARAM_SCOPE


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


def test_missing_arguments_has_no_none() -> None:
    # The other fields drop a sentence on ``None``; this one is a result's
    # whole message, so there is nothing to drop it to.
    with pytest.raises(ImproperlyConfigured, match="missing_arguments must be a string"):
        AgentConventions(missing_arguments=None)  # ty: ignore[invalid-argument-type]


def test_braces_in_a_field_that_is_never_formatted_are_literal() -> None:
    # Only ``missing_arguments`` is formatted, so the others take any text.
    conventions = AgentConventions(handle_line="Pass {ids} on.", query_param_on_pages="{x}")

    assert conventions.handle_line == "Pass {ids} on."
    assert conventions.query_param_on_pages == "{x}"
