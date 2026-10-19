from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import DEFAULT_POOL_SEEDS, PoolSeeds, check_server_arguments

from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding

# Deliberately a union rather than a shared base class, so each binding stays a
# frozen dataclass with its own structure. ``tools/list`` and ``tools/call``
# discriminate at dispatch time.
ToolBindingLike = ToolBinding | SelectorToolBinding | ChainToolBinding


class ToolRegistry:
    """Name to tool binding lookup.

    Holds service, selector and chain bindings in one namespace, rejecting
    duplicates loudly so a misconfigured project surfaces the conflict at
    registration rather than silently shadowing a tool.

    ``pool_seeds`` are the owning server's, which ``MCPServer`` passes: a
    binding's ``server_arguments`` are checked against them, since dispatch
    refuses a parameter a registered seed occupies.
    """

    def __init__(self, *, pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS) -> None:
        self._bindings: dict[str, ToolBindingLike] = {}
        self._pool_seeds: PoolSeeds = pool_seeds

    def register(self, binding: ToolBindingLike) -> None:
        """Add ``binding``, refusing a duplicate name or a binding of server values that cannot hold.

        The ``server_arguments`` check is made here rather than in the
        registrars alone, because a binding built by hand and handed straight to
        ``server.tools.register`` would otherwise reach every ``tools/call``
        unchecked, and fail each one with ``ImproperlyConfigured`` where it
        should have failed the server's startup
        (``test_a_binding_built_by_hand_is_checked_when_it_is_registered``).
        The registrars reach it as their last step, after the spec has compiled.
        """
        if binding.name in self._bindings:
            raise ValueError(f"Duplicate MCP tool name: {binding.name!r}")
        _refuse_server_arguments_that_cannot_hold(binding, self._pool_seeds)
        self._bindings[binding.name] = binding

    def get(self, name: str) -> ToolBindingLike | None:
        return self._bindings.get(name)

    def all(self) -> list[ToolBindingLike]:
        """Every binding, in **registration order**.

        ``tools/list`` has to be deterministic so clients can cache the catalog
        and cursor pagination stays stable, which dict insertion order already
        gives. Deliberately not sorted by name: registration order is authored
        order, which is a better first page for a model than an alphabetical
        one.
        """
        return list(self._bindings.values())

    def __len__(self) -> int:
        return len(self._bindings)

    def __contains__(self, name: object) -> bool:
        return name in self._bindings


def _refuse_server_arguments_that_cannot_hold(
    binding: ToolBindingLike, pool_seeds: PoolSeeds
) -> None:
    """Raise ``ImproperlyConfigured`` naming the tool for a binding of server values that cannot hold.

    The kernel's ``check_server_arguments`` decides, and names the spec's kind
    and callable; this adds the tool's name, which is what a server binding
    many tools needs to find the one at fault.

    One arc to coverage, so each condition names its holder. A chain tool
    carries none, its steps being specs of their own: without the first,
    every chain registration fails on the missing attribute
    (``test_chain_of_only_selectors_is_read_only``). A binding that binds
    nothing is not asked, because the kernel's check compiles the spec's
    parameters and would answer a declaration this package refuses later in
    its own words: without the second,
    ``test_a_lookup_key_declared_as_a_serializer_field_is_refused`` gets the
    kernel's message.
    """
    if isinstance(binding, ChainToolBinding) or not binding.server_arguments.names():
        return
    try:
        check_server_arguments(binding.spec, binding.server_arguments, pool_seeds=pool_seeds)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"MCP tool {binding.name!r}: {exc}") from exc


__all__ = ["ToolBindingLike", "ToolRegistry"]
