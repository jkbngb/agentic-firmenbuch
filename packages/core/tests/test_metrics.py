"""RU metering hook (T5), incl. the cross-thread contract search_companies relies on.

``search_companies`` runs COUNT + page on a ThreadPoolExecutor. ContextVars are per-thread, so
the workers must run in a COPIED context for their ``add_ru`` to reach the caller's accumulator.
This locks that behavior in — the subtle bit that makes ru_total correct under T4's parallelism.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

from fbl_core.metrics import add_ru, get_ru, reset_ru_capture, start_ru_capture


def test_noop_without_capture() -> None:
    # No active capture → add_ru is a no-op and get_ru is 0.0 (offline/pipeline default).
    add_ru(123.0)
    assert get_ru() == 0.0


def test_accumulates_in_current_context() -> None:
    token = start_ru_capture()
    try:
        add_ru(10.0)
        add_ru(2.5)
        assert get_ru() == 12.5
    finally:
        reset_ru_capture(token)
    assert get_ru() == 0.0  # reset restores the no-capture state


def test_accumulates_across_pool_workers_with_copied_context() -> None:
    token = start_ru_capture()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            # Same pattern as search_companies: each worker runs in its own copied context, which
            # shares the accumulator object → their charges land in this context's total.
            f1 = pool.submit(contextvars.copy_context().run, add_ru, 100.0)
            f2 = pool.submit(contextvars.copy_context().run, add_ru, 55.0)
            f1.result()
            f2.result()
        assert get_ru() == 155.0
    finally:
        reset_ru_capture(token)


def test_worker_without_copied_context_does_not_leak() -> None:
    # A plain submit (NO copied context) runs in a fresh worker context: its add_ru finds no
    # accumulator and is silently dropped — proving the copy_context() in search.py is load-bearing.
    token = start_ru_capture()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(add_ru, 999.0).result()
        assert get_ru() == 0.0
    finally:
        reset_ru_capture(token)
