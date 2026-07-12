"""Cross-cutting RU-metering hook (telemetry T5).

``CosmosStore.query`` adds each page's ``x-ms-request-charge`` here so the MCP server can read
the total RU a single tool call cost — *without* ``core`` having to depend on the server. The
accumulator lives in a :class:`contextvars.ContextVar`; when nothing has started a capture for
the current context (offline stages, tests, direct queries) every call is a cheap no-op.

Thread note: ``search_companies`` runs its COUNT + page queries on a ``ThreadPoolExecutor``.
ContextVars are per-thread, so the workers must run inside a *copied* context
(``contextvars.copy_context().run(...)``) for their RU to land in the caller's accumulator. The
copy shares the same :class:`_RuAccumulator` object (copy_context copies the binding, not the
object), and :meth:`_RuAccumulator.add` is lock-guarded, so concurrent increments are safe.
"""

from __future__ import annotations

import contextvars
import threading


class _RuAccumulator:
    """A lock-guarded running total of request charges for one captured context."""

    __slots__ = ("_lock", "total")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0.0

    def add(self, charge: float) -> None:
        with self._lock:
            self.total += charge


_ru_var: contextvars.ContextVar[_RuAccumulator | None] = contextvars.ContextVar(
    "fbl_ru_accumulator", default=None
)


def start_ru_capture() -> contextvars.Token[_RuAccumulator | None]:
    """Begin accumulating RU for this context; returns a token for :func:`reset_ru_capture`."""
    return _ru_var.set(_RuAccumulator())


def add_ru(charge: float) -> None:
    """Add a request charge to the active accumulator (no-op if none is active)."""
    acc = _ru_var.get()
    if acc is not None and charge:
        acc.add(charge)


def get_ru() -> float:
    """The RU accumulated since :func:`start_ru_capture` in this context (0.0 if not capturing)."""
    acc = _ru_var.get()
    return acc.total if acc is not None else 0.0


def reset_ru_capture(token: contextvars.Token[_RuAccumulator | None]) -> None:
    """Undo the accumulator set by :func:`start_ru_capture`."""
    _ru_var.reset(token)
