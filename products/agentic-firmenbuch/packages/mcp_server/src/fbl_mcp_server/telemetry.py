"""Per-tool-call telemetry: latency, RU, result shape, and LLM-rounds-per-session (T5).

One custom event/span per MCP tool call carrying: ``tool``, ``duration_ms``, ``ru_total``,
``result_total``, ``zero_hit``, ``page``, ``filters_used`` (**field NAMES only, never values** —
privacy), ``plan``, and ``mcp_session_id`` (the streamable-HTTP session id, which IS the key for
"how many tool calls did one LLM conversation take"). Everything is optional and lazy: with no
App Insights connection string configured, :func:`tool_span` is a near-zero-cost no-op and
nothing here imports OpenTelemetry, so offline tests and the pipeline never take the dependency.

Wiring (``app.py``):
* :func:`configure_telemetry` once at startup (wires ``azure-monitor-opentelemetry`` if the
  connection string is set).
* :func:`set_session_id` from the request headers (done in ``_http_token``) and
  :func:`set_plan` from ``McpService._authorize`` populate the two request-scoped ContextVars.
* the :func:`instrumented` decorator wraps every ``McpService`` tool method uniformly.
"""

from __future__ import annotations

import contextvars
import functools
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fbl_core.config import Settings
from fbl_core.metrics import get_ru, reset_ru_capture, start_ru_capture

# Request-scoped context (set by app.py per call; default None when absent, e.g. stdio/tests).
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fbl_mcp_session_id", default=None
)
_plan: contextvars.ContextVar[str | None] = contextvars.ContextVar("fbl_mcp_plan", default=None)


def set_session_id(session_id: str | None) -> None:
    _session_id.set(session_id or None)


def set_plan(plan: str | None) -> None:
    _plan.set(plan or None)


@dataclass
class _State:
    enabled: bool = False
    # A test/inspection sink: when set, events are recorded here regardless of `enabled`, so the
    # attribute computation can be asserted without App Insights or OpenTelemetry installed.
    recent: deque[dict[str, Any]] | None = None


_STATE = _State()


def configure_telemetry(settings: Settings) -> bool:
    """Wire Azure Monitor if a connection string is configured. Returns True when enabled.

    Import of ``azure.monitor.opentelemetry`` is deliberately lazy and swallowed on failure so a
    missing optional dependency never breaks startup — telemetry simply stays off."""
    conn = settings.appinsights_connection_string
    if not conn:
        _STATE.enabled = False
        return False
    try:  # pragma: no cover - exercised only where the optional dep + env are present
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)
        _STATE.enabled = True
    except Exception:
        _STATE.enabled = False
    return _STATE.enabled


def enable_test_sink(maxlen: int = 64) -> deque[dict[str, Any]]:
    """Route emitted events into an in-memory deque (for tests). Returns the deque."""
    _STATE.recent = deque(maxlen=maxlen)
    return _STATE.recent


def disable_test_sink() -> None:
    _STATE.recent = None


def _active() -> bool:
    return _STATE.enabled or _STATE.recent is not None


@dataclass
class _Span:
    """Mutable per-call observation the decorator fills in; emitted on context exit."""

    tool: str
    attrs: dict[str, Any] = field(default_factory=dict)

    def observe(self, *, args: tuple[Any, ...], kwargs: dict[str, Any], result: Any) -> None:
        """Derive privacy-safe attributes from the call's inputs and its result dict."""
        filters = _find_filters(args, kwargs)
        if filters is not None:
            # NAMES of the explicitly-set filters only — never their values. `status` defaults to
            # "all"; count it only when the caller set it to something else.
            names = sorted(n for n in filters.model_fields_set if not _is_default(filters, n))
            self.attrs["filters_used"] = ",".join(names)
        page = kwargs.get("page")
        if isinstance(result, dict):
            total = result.get("total")
            if isinstance(total, int):
                self.attrs["result_total"] = total
                self.attrs["zero_hit"] = total == 0
            page = result.get("page", page)
        if isinstance(page, int):
            self.attrs["page"] = page


class _NullSpan:
    def observe(self, **_kwargs: Any) -> None:  # pragma: no cover - trivial
        pass


@contextmanager
def tool_span(tool: str) -> Iterator[_Span | _NullSpan]:
    """Measure one tool call. No-op (no RU capture, no timing) unless telemetry is active."""
    if not _active():
        yield _NullSpan()
        return
    token = start_ru_capture()
    t0 = time.perf_counter()
    span = _Span(tool=tool)
    try:
        yield span
    finally:
        span.attrs["tool"] = tool
        span.attrs["duration_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        span.attrs["ru_total"] = round(get_ru(), 2)
        span.attrs["mcp_session_id"] = _session_id.get()
        span.attrs["plan"] = _plan.get()
        reset_ru_capture(token)
        _emit(span.attrs)


def _emit(attrs: dict[str, Any]) -> None:
    if _STATE.recent is not None:
        _STATE.recent.append(dict(attrs))
    if _STATE.enabled:  # pragma: no cover - requires OpenTelemetry + configured exporter
        try:
            from opentelemetry import trace  # type: ignore[import-not-found]

            tracer = trace.get_tracer("fbl_mcp_server.tools")
            with tracer.start_as_current_span(f"tool.{attrs['tool']}") as span:
                for k, v in attrs.items():
                    if v is not None:
                        span.set_attribute(f"fbl.{k}", v)
        except Exception:
            pass


def instrumented[F: Callable[..., Any]](func: F) -> F:
    """Wrap a ``McpService`` tool method so every call emits a telemetry event. The tool name is
    the method name; ``self`` and the leading ``token`` arg are passed through untouched."""

    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with tool_span(func.__name__) as span:
            result = func(self, *args, **kwargs)
            span.observe(args=args, kwargs=kwargs, result=result)
            return result

    return wrapper  # type: ignore[return-value]


def _find_filters(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """Return the SearchFilters-like argument (has model_fields_set) if the call carried one."""
    for candidate in (*args, *kwargs.values()):
        cls = type(candidate)
        if hasattr(candidate, "model_fields_set") and hasattr(cls, "model_fields"):
            return candidate
    return None


def _is_default(model: Any, name: str) -> bool:
    """True if field ``name`` still holds its declared default (so it isn't an 'active' filter)."""
    field_info = model.__class__.model_fields.get(name)
    if field_info is None:
        return False
    return bool(getattr(model, name) == field_info.default)
