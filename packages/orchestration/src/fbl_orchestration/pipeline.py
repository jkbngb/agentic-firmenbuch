"""The processing pipeline: consolidate → derive → present over a set of FNRs (§8.8).

Stages run sequentially per company; one bad company dead-letters without failing the
run. ``CohortStats`` is computed once per run over the whole consolidated universe.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from fbl_consolidate import consolidate
from fbl_core.lineage import now_utc_z
from fbl_core.logging import get_logger
from fbl_core.models import ConsolidatedCompany
from fbl_core.storage import BlobStoreLike, CosmosStoreLike
from fbl_derive import build_cohort_stats, derive
from fbl_firmenbuch_client import RegisterSource
from fbl_present import present, present_status_only
from fbl_registry import Registry

from .loaders import load_master, load_prev, parse_all

CONSOLIDATED, DERIVED, PRESENTED = "50_consolidated", "30_derived", "10_presentation"


@dataclass
class PipelineContext:
    """Injected dependencies — Azure stores + API in prod, fakes in tests."""

    blob: BlobStoreLike
    cosmos: CosmosStoreLike
    source: RegisterSource
    registry: Registry
    expose_personal_data: bool = False
    growth_horizons: list[int] = field(default_factory=lambda: [1, 3, 5])
    current_year: int | None = None
    # change_feed daily-delta overlap (days re-checked each run); see Settings.delta_lookback_days.
    delta_lookback_days: int = 3
    # Optional lease renewal, called every HEARTBEAT_EVERY companies during a long run
    # (§15a.3). The orchestrator wires this to ``heartbeat_run_lock``; tests leave it None.
    heartbeat: Callable[[], bool] | None = None


HEARTBEAT_EVERY = 50  # renew the run lease after this many companies
log = get_logger("orchestration.pipeline")


class ProcessReport(BaseModel):
    run_id: str
    consolidated: int = 0
    processed: int = 0
    status_only_refreshed: int = 0
    failures: int = 0
    throttled: int = 0
    dead_letters: list[str] = []


def _is_throttle(exc: Exception) -> bool:
    """True for a transient Cosmos 429 (rate limit).

    A throttle is **not** a data failure — the FNR must be retried on the next run, never
    dead-lettered (else the serverless RU ceiling permanently drops companies).
    """
    return getattr(exc, "status_code", None) == 429 or "TooManyRequests" in str(exc)


def _upsert(cosmos: CosmosStoreLike, container: str, fnr: str, doc: dict[str, object]) -> None:
    cosmos.upsert(container, {**doc, "id": fnr, "fnr": fnr})


def process_set(
    ctx: PipelineContext,
    run_id: str,
    fnrs: list[str],
    *,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ProcessReport:
    """Consolidate the set, build the universe cohort, then derive + present each.

    ``max_seconds`` makes the run **self-bounding**: when the budget is spent it stops cleanly,
    leaving the unprocessed companies ``dirty`` so the next run resumes them. The dirty set IS
    the checkpoint (companies are marked clean only after a successful present), so a partial run
    never loses or double-counts work — and the platform replica-timeout can never hard-kill it
    mid-company. Without a budget (``None``) it processes the whole set in one pass (backfill/test).
    """
    report = ProcessReport(run_id=run_id)
    year = ctx.current_year or int(now_utc_z()[:4])
    deadline = (clock() + max_seconds) if max_seconds is not None else None

    def _out_of_time() -> bool:
        return deadline is not None and clock() >= deadline

    # 1) Consolidate the dirty set (facts).
    for i, fnr in enumerate(fnrs):
        if _out_of_time():
            log.warning(
                "time budget reached during consolidate; deferring rest to next run (still dirty)",
                extra={"context": {"deferred": len(fnrs) - i}},
            )
            break
        if ctx.heartbeat is not None and i and i % HEARTBEAT_EVERY == 0:
            ctx.heartbeat()  # renew the lease so a long run is not overtaken
        try:
            filings = parse_all(ctx.blob, fnr, run_id=run_id)
            cons = consolidate(
                fnr,
                filings,
                load_master(ctx.blob, fnr),
                load_prev(ctx.cosmos, fnr),
                run_id=run_id,
            )
            _upsert(ctx.cosmos, CONSOLIDATED, fnr, cons.model_dump(mode="json", exclude_none=True))
            report.consolidated += 1
        except Exception as exc:
            ctx.registry.dead_letter(fnr, f"consolidate: {exc}")
            report.failures += 1
            report.dead_letters.append(fnr)

    # 2) Cohort over the whole consolidated universe (once per run, §8.6).
    universe = [ConsolidatedCompany.model_validate(d) for d in ctx.cosmos.iter_all(CONSOLIDATED)]
    cohort = build_cohort_stats(universe)

    # 3) Derive + present each successfully-consolidated company.
    for i, fnr in enumerate(fnrs):
        if _out_of_time():
            log.warning(
                "time budget reached during derive/present; deferring rest (still dirty)",
                extra={"context": {"deferred": len(fnrs) - i}},
            )
            break
        if ctx.heartbeat is not None and i and i % HEARTBEAT_EVERY == 0:
            ctx.heartbeat()
        raw = ctx.cosmos.get(CONSOLIDATED, fnr)
        if raw is None:
            continue  # consolidate failed -> already dead-lettered
        try:
            cons = ConsolidatedCompany.model_validate(raw)
            der = derive(
                cons, cohort_stats=cohort, growth_horizons=ctx.growth_horizons, run_id=run_id
            )
            _upsert(ctx.cosmos, DERIVED, fnr, der.model_dump(mode="json", exclude_none=True))
            reg = ctx.registry.get(fnr)
            pres = present(
                der,
                status=reg.status if reg else None,
                expose_personal_data=ctx.expose_personal_data,
                run_id=run_id,
                current_year=year,
            )
            _upsert(ctx.cosmos, PRESENTED, fnr, pres.model_dump(mode="json", exclude_none=True))
            ctx.registry.mark_clean(fnr)
            report.processed += 1
        except Exception as exc:
            ctx.registry.dead_letter(fnr, f"derive/present: {exc}")
            report.failures += 1
            report.dead_letters.append(fnr)
    return report


class ProcessCheckpoint(Protocol):
    """Two FNR sets for the bulk backfill-process: consolidated, presented (§15a.1)."""

    def load(self) -> tuple[set[str], set[str]]: ...

    def save(self, consolidated: set[str], presented: set[str]) -> None: ...


def _run_pool[T](
    items: Iterable[T],
    work: Callable[[T], Exception | None],
    account: Callable[[T, Exception | None], None],
    *,
    workers: int,
    save_every: int,
    save: Callable[[], None],
    stop: Callable[[], bool],
) -> bool:
    """Run ``work(item)`` over *items*, ``account``-ing each result on the MAIN thread, with a
    durable ``save()`` every ``save_every`` and a ``stop()`` budget. Returns True if every item
    was processed, False if it stopped early (resume next run).

    ``workers > 1`` uses a bounded sliding window (~workers×4 in flight) — same shape as
    ``run_ingest`` (§15a.1): no head-of-line blocking, per-company checkpoint, memory capped
    regardless of worklist size. Each company is a distinct Cosmos partition + blob path, so
    the per-FNR reads/writes inside ``work`` are thread-safe; shared state (done-sets, report,
    checkpoint) is only touched in ``account``/``save`` on the main thread.
    """
    if workers <= 1:
        since = 0
        for item in items:
            account(item, work(item))
            since += 1
            if since >= save_every:
                save()
                since = 0
            if stop():
                save()
                return False
        save()
        return True

    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
    from concurrent.futures import wait as fwait

    src: Iterator[T] = iter(items)
    window = max(workers * 4, workers)
    since = 0
    stopping = False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        inflight = {ex.submit(work, it): it for it in itertools.islice(src, window)}
        while inflight:
            finished, _ = fwait(set(inflight), return_when=FIRST_COMPLETED)
            for fut in finished:
                item = inflight.pop(fut)
                account(item, fut.result())
                since += 1
            if since >= save_every:
                save()
                since = 0
            if not stopping and stop():
                stopping = True  # drain in-flight, submit no more
            if not stopping:
                for it in itertools.islice(src, len(finished)):
                    inflight[ex.submit(work, it)] = it
    save()
    return not stopping


def process_backfill(
    ctx: PipelineContext,
    run_id: str,
    fnrs: list[str],
    *,
    checkpoint: ProcessCheckpoint,
    workers: int = 1,
    save_every: int = 200,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ProcessReport:
    """Bulk consolidate → derive → present over *fnrs*, hardened to **never get stuck**
    (§15a.1, the never-stuck pattern applied to processing):

    * **Parallel** — ``workers > 1`` fans each phase across a bounded thread pool (consolidate
      and derive/present are blob/Cosmos-I/O-bound, so ≈ linear speed-up, like the ingest).
    * **Resumable** — a Blob checkpoint holds two done-sets (consolidated, presented); a
      bounded/killed run resumes mid-stream, losing ≤ ``save_every`` companies.
    * **Bounded** — ``max_seconds`` ends the run cleanly with a saved checkpoint; the next
      scheduled run continues. No run can hang.
    * **Memory-safe** — the cohort is built by **streaming** the consolidated universe one
      doc at a time (never materialising 200k+ company objects — the original OOM trap).
    * **Two-phase** — derive/present only begins once the *whole* worklist is consolidated,
      because the cohort percentiles must be computed over the complete set.

    Deterministic per-company failures (parse/derive) are dead-lettered AND marked done, so
    they are not retried forever.
    """
    report = ProcessReport(run_id=run_id)
    year = ctx.current_year or int(now_utc_z()[:4])
    deadline = (clock() + max_seconds) if max_seconds is not None else None
    worklist = set(fnrs)
    cons_done, pres_done = checkpoint.load()
    cons_done &= worklist  # ignore stale FNRs from a different worklist
    pres_done &= worklist
    # Issue #7: a company whose raw was re-ingested is marked `dirty` by `record_filing`.
    # Honour that over the checkpoint — drop dirty FNRs from the done-sets so their new raw is
    # rebuilt, instead of being skipped as "done" (the bug that left recovered dead-letters
    # master-only until a manual checkpoint eviction). `present` calls `mark_clean`, so a
    # rebuilt company drops out of `dirty` again and won't be reprocessed next run.
    dirty = {f for f in ctx.registry.dirty_fnrs() if f in worklist}
    cons_done -= dirty
    pres_done -= dirty

    def _stop() -> bool:
        if ctx.heartbeat is not None and not ctx.heartbeat():
            return True
        return deadline is not None and clock() >= deadline

    def _save() -> None:
        checkpoint.save(cons_done, pres_done)

    # --- Phase A: consolidate the whole worklist (facts) ---
    def _consolidate(fnr: str) -> Exception | None:
        try:
            filings = parse_all(ctx.blob, fnr, run_id=run_id)
            cons = consolidate(
                fnr, filings, load_master(ctx.blob, fnr), load_prev(ctx.cosmos, fnr), run_id=run_id
            )
            _upsert(ctx.cosmos, CONSOLIDATED, fnr, cons.model_dump(mode="json", exclude_none=True))
            return None
        except Exception as exc:  # returned to the main thread, never crashes the pool
            return exc

    def _account_a(fnr: str, err: Exception | None) -> None:
        if err is not None and _is_throttle(err):
            report.throttled += 1
            return  # transient 429 → leave un-done so the next run retries it
        if err is None:
            report.consolidated += 1
        else:
            ctx.registry.dead_letter(fnr, f"consolidate: {err}")
            report.failures += 1
            report.dead_letters.append(fnr)
        cons_done.add(fnr)  # mark done (success or real failure) → never retried forever

    completed = _run_pool(
        (f for f in fnrs if f not in cons_done),
        _consolidate,
        _account_a,
        workers=workers,
        save_every=save_every,
        save=_save,
        stop=_stop,
    )
    if not completed or not worklist.issubset(cons_done):
        return report  # bounded out, or still consolidating across runs

    # Cohort over the consolidated universe — STREAMED (one doc in memory at a time). The
    # derive workers read it concurrently (read-only), which is safe.
    cohort = build_cohort_stats(
        ConsolidatedCompany.model_validate(d) for d in ctx.cosmos.iter_all(CONSOLIDATED)
    )

    # --- Phase B: derive + present ---
    def _present(fnr: str) -> Exception | None:
        raw = ctx.cosmos.get(CONSOLIDATED, fnr)
        if raw is None:
            return None  # consolidate failed (already dead-lettered) — count as done, skip
        try:
            cons = ConsolidatedCompany.model_validate(raw)
            der = derive(
                cons, cohort_stats=cohort, growth_horizons=ctx.growth_horizons, run_id=run_id
            )
            _upsert(ctx.cosmos, DERIVED, fnr, der.model_dump(mode="json", exclude_none=True))
            reg = ctx.registry.get(fnr)
            pres = present(
                der,
                status=reg.status if reg else None,
                expose_personal_data=ctx.expose_personal_data,
                run_id=run_id,
                current_year=year,
            )
            _upsert(ctx.cosmos, PRESENTED, fnr, pres.model_dump(mode="json", exclude_none=True))
            ctx.registry.mark_clean(fnr)
            return None
        except Exception as exc:
            return exc

    def _account_b(fnr: str, err: Exception | None) -> None:
        if err is not None and _is_throttle(err):
            report.throttled += 1
            return  # transient 429 → leave un-done so the next run retries it
        if err is None:
            report.processed += 1
        else:
            ctx.registry.dead_letter(fnr, f"derive/present: {err}")
            report.failures += 1
            report.dead_letters.append(fnr)
        pres_done.add(fnr)

    _run_pool(
        (f for f in fnrs if f not in pres_done),
        _present,
        _account_b,
        workers=workers,
        save_every=save_every,
        save=_save,
        stop=_stop,
    )
    return report


def refresh_status_only(ctx: PipelineContext, run_id: str, fnrs: list[str]) -> int:
    """Cheap re-`present` for status-change-only dirty companies (§15a.0)."""
    from fbl_core.models import PresentedCompany

    refreshed = 0
    for fnr in fnrs:
        raw = ctx.cosmos.get(PRESENTED, fnr)
        reg = ctx.registry.get(fnr)
        if raw is None or reg is None:
            continue
        prev = PresentedCompany.model_validate(raw)
        updated = present_status_only(prev, reg.status, run_id=run_id)
        _upsert(ctx.cosmos, PRESENTED, fnr, updated.model_dump(mode="json", exclude_none=True))
        ctx.registry.mark_clean(fnr)
        refreshed += 1
    return refreshed
