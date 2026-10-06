"""Shared assertions and helpers for the test suite."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from rest_framework.permissions import BasePermission

from rest_framework_mcp.conf import get_setting


def tool_error(out: Any) -> dict[str, Any]:
    """Assert ``out`` is an ``isError`` tool result; return its error object.

    Tool-level failures (business rules, service-raised validation,
    missing rows) come back as successful JSON-RPC responses whose result
    carries ``isError: true`` and a JSON error payload in ``content[0]``.
    ``structuredContent`` must be absent — it is tied to the success
    ``outputSchema``.
    """
    assert isinstance(out, dict), f"expected a tool-result dict, got {out!r}"
    assert out.get("isError") is True
    assert "structuredContent" not in out
    return json.loads(out["content"][0]["text"])["error"]


def granting_route(key: str, value: Any, seen: list[dict[str, Any]]) -> type[BasePermission]:
    """A DRF permission granting a route whose ``view.kwargs[key]`` is ``value``.

    Records every ``view.kwargs`` it is shown into ``seen``: a class rather than
    an instance is what a spec declares, and the paths instantiate it at
    different times, so the record lives outside the instance. Reads with
    ``.get`` so a check made against ``{}`` denies rather than raising, which is
    what the routes this stands in for looked like before they were bound.
    """

    class _GrantsOneRoute(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return view.kwargs.get(key) == value

    return _GrantsOneRoute


class RefusingRateLimit:
    """An ``MCPRateLimit`` refusing every call, counting the calls it was asked about.

    Pins the refusal order on a path that judges permissions before it charges a
    quota: a caller the permission denies is told so, and is never charged.
    """

    def __init__(self) -> None:
        self.consumed: int = 0

    def consume(self, request: Any, token: Any) -> int | None:
        self.consumed += 1
        return 30


# How deep a body must nest before ``json.loads`` raises ``RecursionError``
# depends on where the parse runs. Before Python 3.14 the decoder counts levels
# against a fixed limit. From 3.14 it stops when the C stack runs low, so the
# depth follows the stack: about 74,000 levels on an 8 MiB main thread, and
# about 600,000 under ``make``, which raises the soft stack limit to the hard
# one (64 MiB on macOS) for the processes it starts. A body that deep is over
# the 1 MiB default request cap, so the tests that send one decode it on a
# thread that asks for a 4 MiB stack. There the limit is some tens of thousands
# of levels on 3.14, and the count, about 1,000 or 10,000, on earlier Pythons.
#
# Asks, because on Linux the requested size is a floor rather than the size.
# glibc gives a new thread a cached stack left by one that has exited when that
# stack is at least the size requested and at most four times it, so a thread
# asking for 4 MiB can run on 16 MiB after the default-sized threads of a run
# under ``ulimit -s 16384``, and there 100,000 levels decode on 3.14. No fixed
# depth is past the limit on every run, so the depth is found on the thread that
# decodes the body rather than assumed.
_BOUNDED_STACK_BYTES: int = 4 * 1024 * 1024

# Where the doubling starts: about the limit on 3.10 and 3.11, which count to
# about 1,000 levels, and under every later Python's. So the depth found is
# under twice the limit, and the body no larger than doubling has to make it.
_FIRST_NESTING_DEPTH: int = 1_000


def on_a_bounded_stack(call: Callable[[], Any]) -> Any:
    """Run ``call`` on a new thread that asks for a 4 MiB stack; return its result.

    The bound keeps the nested body small. It is a request rather than a size,
    for the reason the comment above gives, so pair it with
    ``nested_past_the_decoders_limit_here`` called on that thread. The previous
    size is restored however ``call`` ends, so no later thread inherits it.
    """
    previous: int = threading.stack_size(_BOUNDED_STACK_BYTES)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(call).result()
    finally:
        threading.stack_size(previous)


def nested_past_the_decoders_limit_here() -> bytes:
    """Return nested arrays ``json.loads`` raises ``RecursionError`` on, on this thread.

    The depth doubles until the decoder raises here, because the limit follows
    the stack of the calling thread and that stack is not known in advance. Call
    it on the thread that will decode the body: the view decodes deeper in the
    call stack than this probe, with no more stack and no more levels left, so a
    body that overflows here also overflows in the view.

    The loop stops at ``MAX_REQUEST_BYTES``. A body over it would be answered by
    the size cap before the parse, and a stack too large to reach the limit under
    it would otherwise send one or never end, so that case fails the test instead.
    """
    cap: int = get_setting("MAX_REQUEST_BYTES")
    depth: int = _FIRST_NESTING_DEPTH
    while 2 * depth <= cap:
        body: bytes = b"[" * depth + b"]" * depth
        try:
            json.loads(body)
        except RecursionError:
            return body
        depth *= 2
    pytest.fail(
        f"nested arrays up to the {cap}-byte request cap all decoded on this thread: "
        "its stack is too large for the decoder's recursion limit to be reached"
    )
