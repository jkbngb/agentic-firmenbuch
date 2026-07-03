"""``sync_registry`` — idempotent upsert/diff of 99_registry vs the authoritative list.

One operation, two regimes (§15a.0): on an empty registry the first run is the full
**seed**; every run after is a **reconcile** (add missing FNRs, refresh
``last_seen_in_registry``, mark vanished FNRs ``deleted``).

The **bulk dataset** is the preferred, completeness-safe source (pass ``bulk=...``); the
prefix-walk is the fallback. After a walk, a completeness self-check logs per-Rechtsform
counts and every branch that hit the depth ceiling.
"""

from __future__ import annotations

from collections.abc import Callable

from fbl_core.lineage import now_utc_z
from fbl_core.logging import get_logger
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike
from fbl_firmenbuch_client import FirmaResult, RegisterSource
from fbl_registry import Registry, RegistryStatus

from .bulk import BulkSource
from .checkpoint import WALK_COMPLETE_MARKER
from .enumerate import DEFAULT_RECHTSFORMEN, Checkpoint, prefix_walk
from .models import DriftEntry, SyncReport

log = get_logger("ingest.sync_registry")

# Where the "what the change feed missed" drift reports are archived (§15a.1).
DRIFT_REPORT_DIR = "_reports/sync-registry"


def status_from_result(result: FirmaResult) -> RegistryStatus:
    """Map a ``sucheFirma`` STATUS string to the registry status."""
    return _status(result.status)


def _status(raw_status: str | None) -> RegistryStatus:
    raw = (raw_status or "").strip().lower()
    if "gelösch" in raw or "geloesch" in raw:
        return "deleted"
    if "histor" in raw:
        return "historical"
    return "active"


def sync_registry(
    source: RegisterSource,
    registry: Registry,
    *,
    bulk: BulkSource | None = None,
    rechtsformen: tuple[str, ...] = DEFAULT_RECHTSFORMEN,
    source_label: str | None = None,
    mark_vanished_deleted: bool = True,
    checkpoint: Checkpoint | None = None,
    report_blob: BlobStoreLike | None = None,
    run_id: str | None = None,
    heartbeat: Callable[[], bool] | None = None,
) -> SyncReport:
    """Seed (first run) or reconcile (later runs) the registry against the universe.

    Prefers the bulk dataset when *bulk* is provided; otherwise prefix-walks the API.
    When *report_blob* is given, the drift report ("what the change feed missed") is
    archived to ``90-raw/_reports/sync-registry/{run_id}.json``.
    """
    seen_at = now_utc_z()  # full ISO-8601 Z timestamp
    # An empty registry means this is the initial seed, not a reconcile: every company is
    # "new", so the seeded list would be the whole universe — not real change-feed drift.
    was_initial = registry.count() == 0
    if bulk is not None:
        report = _sync_from_bulk(
            bulk, registry, seen_at, mark_vanished_deleted, source_label or "hvd_bulk", was_initial
        )
    else:
        report = _sync_from_walk(
            source,
            registry,
            seen_at,
            rechtsformen,
            mark_vanished_deleted,
            source_label or "sucheFirma_sweep",
            checkpoint,
            was_initial,
            heartbeat,
        )
    if report_blob is not None:
        _write_drift_report(
            report, report_blob, run_id or seen_at.replace(":", "").replace("-", "")
        )
        # Reaching here = a full seed/reconcile finished (a crashed walk never returns).
        # The marker lets the active-only backfill start itself once the grinder is done.
        report_blob.put_json(
            RAW_CONTAINER,
            WALK_COMPLETE_MARKER,
            {"completed_at": seen_at, "total_seen": report.total_seen, "run_id": run_id},
        )
    return report


def _sync_from_walk(
    source: RegisterSource,
    registry: Registry,
    seen_at: str,
    rechtsformen: tuple[str, ...],
    mark_vanished_deleted: bool,
    source_label: str,
    checkpoint: Checkpoint | None,
    was_initial: bool,
    heartbeat: Callable[[], bool] | None = None,
) -> SyncReport:
    report = SyncReport(source=source_label, was_initial_seed=was_initial)

    # Stream companies into the registry AS THEY ARE DISCOVERED (best practice for a long
    # walk): durable + observable + low-burst, and self-healing on restart since upserts are
    # idempotent. mark-vanished still runs at the END over the full seen-set — and only on a
    # COMPLETE walk (a crash means prefix_walk never returns, so nothing is falsely deleted).
    def sink(results: list[FirmaResult]) -> None:
        for r in results:
            _upsert_one(
                registry,
                r.fnr,
                _status(r.status),
                r.name,
                r.rechtsform_code,
                seen_at,
                source_label,
                report,
            )

    walk = prefix_walk(
        source, rechtsformen=rechtsformen, checkpoint=checkpoint, on_found=sink, heartbeat=heartbeat
    )
    report.total_seen = len(walk.found)
    report.incomplete_branches = walk.incomplete
    report.counts_by_rechtsform = walk.counts_by_rechtsform
    if mark_vanished_deleted:
        _mark_vanished(registry, set(walk.found), report)

    # Completeness self-check (§B5): counts per Rechtsform + flag ceiling branches.
    log.info(
        "sync_registry completeness",
        extra={
            "context": {
                "total_seen": report.total_seen,
                "counts_by_rechtsform": report.counts_by_rechtsform,
                "incomplete_branches": len(report.incomplete_branches),
            }
        },
    )
    if report.incomplete_branches:
        log.error(
            "enumeration incomplete — branches hit the depth ceiling (companies may be missed)",
            extra={"context": {"branches": report.incomplete_branches[:50]}},
        )
    return report


def _sync_from_bulk(
    bulk: BulkSource,
    registry: Registry,
    seen_at: str,
    mark_vanished_deleted: bool,
    source_label: str,
    was_initial: bool,
) -> SyncReport:
    report = SyncReport(source=source_label, was_initial_seed=was_initial)
    seen: dict[str, tuple[RegistryStatus, str | None, str | None]] = {}
    for company in bulk.iter_companies():
        seen[company.fnr] = (_status(company.status), company.name, company.rechtsform)
        rf = company.rechtsform or ""
        report.counts_by_rechtsform[rf] = report.counts_by_rechtsform.get(rf, 0) + 1
    report.total_seen = len(seen)
    _upsert_seen(registry, seen, seen_at, source_label, report)
    if mark_vanished_deleted:
        _mark_vanished(registry, set(seen), report)
    log.info(
        "sync_registry (bulk) completeness",
        extra={"context": {"total_seen": report.total_seen, "source": source_label}},
    )
    return report


def _upsert_one(
    registry: Registry,
    fnr: str,
    status: RegistryStatus,
    name: str | None,
    rechtsform: str | None,
    seen_at: str,
    source_label: str,
    report: SyncReport,
) -> None:
    """Idempotently upsert one company into the registry (new → seed, else refresh)."""
    existing = registry.get(fnr)
    if existing is None:
        doc = registry.ensure(
            fnr, status=status, source=source_label, name=name, rechtsform=rechtsform
        )
        report.seeded += 1
        # On a reconcile, a newly-seeded company is one the change feed should have caught
        # as a Neueintragung but didn't = drift. (Skip on the initial seed: it's everything.)
        if not report.was_initial_seed:
            report.seeded_companies.append(
                DriftEntry(fnr=fnr, name=name, rechtsform=rechtsform, status=status)
            )
    else:
        doc = existing
        doc.status = status
        if name:  # refresh the catalog name; never overwrite a known name with None
            doc.name = name
        if rechtsform:  # likewise the legal-form code
            doc.rechtsform = rechtsform
        report.updated += 1
    doc.last_seen_in_registry = seen_at
    registry.put(doc)


def _upsert_seen(
    registry: Registry,
    seen: dict[str, tuple[RegistryStatus, str | None, str | None]],
    seen_at: str,
    source_label: str,
    report: SyncReport,
) -> None:
    for fnr, (status, name, rechtsform) in seen.items():
        _upsert_one(registry, fnr, status, name, rechtsform, seen_at, source_label, report)


def _mark_vanished(registry: Registry, seen: set[str], report: SyncReport) -> None:
    """FNRs in the registry but not in the authoritative source → deleted (reconcile)."""
    for doc in registry.iter_docs():
        if doc.fnr not in seen and doc.status != "deleted":
            doc.status = "deleted"
            registry.put(doc)
            report.marked_deleted += 1
            # A deletion the full sweep caught that wasn't already deleted = a Löschung the
            # change feed missed (drift). Always recorded — deletions are rare even on a seed.
            report.deleted_companies.append(
                DriftEntry(fnr=doc.fnr, name=doc.name, rechtsform=doc.rechtsform, status="deleted")
            )


def _write_drift_report(report: SyncReport, blob: BlobStoreLike, run_id: str) -> None:
    """Archive the drift report to Blob + log a summary (§15a.1).

    "Drift" = what the daily change feed missed: companies the full reconcile had to add
    (``seeded_companies``) or mark deleted (``deleted_companies``). On the initial seed the
    registry was empty, so ``was_initial_seed`` is set and ``seeded_companies`` is omitted
    (it would be the entire universe, not drift).
    """
    path = f"{DRIFT_REPORT_DIR}/{run_id}.json"
    blob.put_json(RAW_CONTAINER, path, report.model_dump(mode="json"))
    log.info(
        "sync_registry drift report",
        extra={
            "context": {
                "report_path": f"{RAW_CONTAINER}/{path}",
                "was_initial_seed": report.was_initial_seed,
                "missed_new_companies": len(report.seeded_companies),
                "missed_deletions": len(report.deleted_companies),
                "total_seen": report.total_seen,
            }
        },
    )
