from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


# OpenTelemetry is an optional extra; the package must remain usable without
# it. Resolve the trace module via ``importlib`` so the binding is plain
# ``Any`` (or ``None``) — no narrowing for the type checker to fight, and
# the no-extras smoke job still exercises the ``None`` fallback.
def _resolve_otel_trace() -> Any:
    try:
        return importlib.import_module("opentelemetry.trace")
    except ImportError:  # pragma: no cover - exercised by the no-extras smoke job
        return None


_otel_trace: Any = _resolve_otel_trace()

# Tracer name follows the package's import path so OTel collectors can group
# spans coherently. ``__version__`` is included so users can correlate trace
# data with a specific release.
_TRACER_NAME: str = "rest_framework_mcp"


class _NoopSpan:
    """Fallback span used when ``opentelemetry`` is not installed.

    Mirrors the subset of the OTel ``Span`` API the handlers use:
    ``set_attribute``, ``set_status``, ``record_exception``. Every method is
    a no-op so caller code stays branch-free.
    """

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_status(self, status: Any) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Start an MCP span; no-op if OpenTelemetry isn't installed.

    ``name`` follows the convention ``mcp.<method>`` (``mcp.tools.call``,
    ``mcp.resources.read``, ``mcp.prompts.get``). Attributes are set on the
    span at start time; handlers can grab the yielded span object to record
    additional fields (binding name, error code, etc.) before exit.
    """
    if _otel_trace is None:
        yield _NoopSpan()
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name, attributes=attributes or {}) as otel_span:
        yield otel_span


__all__ = ["span"]
