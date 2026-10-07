"""Shared validation and derivation for the three tool bindings.

In a sibling ``utils.py`` rather than its own leaf module because it is
internal infrastructure for ``registry.types``, not part of the exported
type surface.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import can_present_nothing as spec_can_present_nothing
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ToolContentKind

_MEDIA_KINDS: frozenset[ToolContentKind] = frozenset({ToolContentKind.IMAGE, ToolContentKind.AUDIO})


def validate_content_kind(
    *,
    name: str,
    content_kind: ToolContentKind,
    content_mime_type: str | None,
    include_structured_content: bool | None,
    include_output_schema: bool | None,
) -> None:
    """Refuse a content-kind declaration that cannot produce a valid result.

    Called from each binding's ``__post_init__``. Both checks are
    contradictions rather than judgement calls:

    - **Media needs a mime type.** ``mimeType`` is required on an ``image`` /
      ``audio`` block; without it the client holds a base64 string and no way
      to know what it decodes to.
    - **Media has no JSON projection.** ``structuredContent`` and
      ``outputSchema`` describe a JSON payload, and a tool returning a PNG has
      none. Both are suppressed for media kinds, so asking for either is a
      declaration that cannot be honoured.
    """
    if content_kind not in _MEDIA_KINDS:
        return
    if not content_mime_type:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} requires "
            "content_mime_type — the MCP spec makes mimeType mandatory on an "
            'image/audio block. Pass e.g. content_mime_type="image/png".'
        )
    if include_structured_content is True or include_output_schema is True:
        raise ImproperlyConfigured(
            f"Tool {name!r}: content_kind={content_kind.name} cannot be combined "
            "with include_structured_content=True or include_output_schema=True. "
            "Both describe a JSON result shape, and this tool returns binary "
            "media instead — there is nothing for them to describe."
        )


def rendered_kind(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> SelectorKind:
    """Whether ``spec``'s rendered result is one object or a list of them.

    The cardinality drf-services' ``dispatch_spec`` gives the result, which is
    what the payload is rendered ``many=`` by:

    - A ``SelectorSpec`` answers its own ``kind``.
    - A ``many=True`` ``ServiceSpec`` answers ``LIST``. Its result is the list the
      service returns, rendered ``many``, and no re-fetch runs; its
      ``output_selector_spec`` is ``RETRIEVE`` by convention, because that kind
      describes one row of it. drf-services' own ``spec_to_json_schema`` answers
      the output phase the same way.
    - Any other ``ServiceSpec`` answers ``LIST`` when its ``output_selector_spec``
      declares ``LIST``, with a selector or without one: the re-fetch produces
      the set, and with no selector drf-services presents the service's own
      return as the set the declaration names (``kind="list"``), refusing a
      return that is not one. Anything else is a single object. The selector is
      not read: ``test_a_list_output_spec_with_no_selector_renders_and_advertises_a_list``
      fails for a service tool and a chain alike if it is. The guard is one
      ``and``-chain, so each conjunct is held by a row of
      ``test_the_rendered_kind_is_the_one_dispatch_gives``: ``no-output-spec``
      (``nested is not None``) and ``retrieve-output-no-re-read`` (the kind).

    One answer read by both halves of a tool -- each binding's ``rendered_kind``,
    which picks the advertised ``outputSchema`` shape, and the chain renderer,
    which picks ``many`` -- because they were once answered separately. Only a
    selector tool's schema was kind-aware, so every other ``LIST`` output
    advertised one object while serving an array, and a chain rendered a
    service step's ``LIST`` re-fetch as a single object and failed.
    """
    if isinstance(spec, SelectorSpec):
        return spec.kind
    if spec.many:
        return SelectorKind.LIST
    nested = spec.output_selector_spec
    if nested is not None and nested.kind is SelectorKind.LIST:
        return SelectorKind.LIST
    return SelectorKind.RETRIEVE


def can_present_nothing(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> bool:
    """Whether a successful dispatch of ``spec`` can present nothing (``None``).

    drf-services' ``can_present_nothing`` answers, so this server's
    ``outputSchema`` admits ``{}`` exactly where drf-services' own output
    schema admits ``null``: a ``RETRIEVE``
    selector under ``allow_none``, a single-row service whose output re-read
    has a ``selector`` (dispatch materializes it with ``.first()``), and a
    single-row service presenting its own return that declares
    ``ServiceSpec(allow_none=True)``. A list result never does, empty at worst.

    A service returning ``None`` without the declaration is still served
    ``{}`` against a strict schema: drf-services presents an undeclared
    ``None`` rather than refusing it, and admitting ``{}`` for every such
    service would turn every row field optional for each client generating
    types from the schema. Held by
    ``test_a_service_declaring_allow_none_serves_an_empty_object_its_schema_admits``
    beside ``test_a_service_with_no_output_reread_keeps_its_schema_strict``.

    What stays this server's is what it does with the answer, and where it
    asks: the schema keeps its root an object and admits ``{}`` rather than
    ``null`` (``build_output_schema``), and a chain asks of its output step's
    spec, and answers ``False`` under ``output_all``, whose ``{alias:
    rendered}`` object is never ``None`` (``ChainToolBinding``).
    """
    return spec_can_present_nothing(spec)


__all__ = ["can_present_nothing", "rendered_kind", "validate_content_kind"]
