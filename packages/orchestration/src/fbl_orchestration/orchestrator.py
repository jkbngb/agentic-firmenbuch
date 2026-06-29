"""The single Job entrypoint: dispatch on ``--mode``, hold the run lock (§8.8, §15a)."""

from __future__ import annotations

import os
import time
import uuid
from datetime import date, timedelta

from fbl_core.lineage import now_utc_z
from fbl_core.logging import get_logger
from fbl_core.storage import RAW_CONTAINER, BlobStoreLike
from fbl_ingest import (
    CHECKPOINT_PATH,
    DEFAULT_RECHTSFORMEN,
    INGEST_FI_CHECKPOINT_PATH,
    PUBLICATION_PRIORITY_RECHTSFORMEN,
    WALK_COMPLETE_MARKER,
    BlobIngestCheckpoint,
    BlobProcessCheckpoint,
    BlobWalkCheckpoint,
    archive_raw_responses,
    detect_changes,
    run_ingest,
    sync_registry,
)

from .pipeline import (
    PipelineContext,
    ProcessReport,
    process_backfill,
    process_set,
    refresh_status_only,
)
from .runlock import heartbeat_run_lock, run_lock

MODES = (
    "sync-registry",
    "backfill-ingest",
    "ingest-fi",
    "backfill-process",
    "directories",
    "daily",
    "diag",
    "diag-doctypes",
)

log = get_logger("orchestration")

# Candidate RECHTSFORM codes (the AI-generated reference is unreliable) + ORTNR (location)
# values to probe — count how many a form-less "*" search returns under each, so we can pick
# the cheapest complete enumeration (correct per-form codes vs. ORTNR form-less partitioning).
_DIAG_RECHTSFORM = (
    "GES",
    "AKT",
    "AG",
    "KG",
    "KEG",
    "OG",
    "OHG",
    "GEN",
    "EGE",
    "EGEN",
    "GENO",
    "PST",
    "PRV",
    "SE",
    "EU",
    "ERW",
    "SPA",
    "VVG",
    "VER",
)
_DIAG_ORTNR = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "700", "900")


def _run_diag(ctx: PipelineContext) -> None:
    """Read-only probe: log result counts per RECHTSFORM code and per ORTNR (form-less)."""
    src = ctx.source
    for rf in _DIAG_RECHTSFORM:
        try:
            n = len(src.suche_firma("*", suchbereich=1, rechtsform=rf, exaktesuche=True))
            log.info("DIAG", extra={"context": {"kind": "rechtsform", "code": rf, "count": n}})
        except Exception as exc:
            ctx_err = {"kind": "rechtsform", "code": rf, "err": str(exc)[:140]}
            log.info("DIAG", extra={"context": ctx_err})
    for o in _DIAG_ORTNR:
        try:
            n = len(src.suche_firma("*", suchbereich=1, ortnr=o, rechtsform="", exaktesuche=True))
            log.info("DIAG", extra={"context": {"kind": "ortnr", "code": o, "count": n}})
        except Exception as exc:
            log.info("DIAG", extra={"context": {"kind": "ortnr", "code": o, "err": str(exc)[:140]}})


def _run_doctypes(ctx: PipelineContext) -> None:
    """Read-only probe: which DOKUMENTART (document-type) codes does ``sucheUrkunde`` actually
    return on our HVD tier, across a sample of every Rechtsform? Answers "what other document
    types beyond 48 (Jahresabschluss) are available to us" (§15b). Logs an aggregate Counter."""
    from collections import Counter

    forms = ("GES", "AG", "KG", "OG", "GEN", "SE", "EU", "PST", "SPA", "VER")
    per_form = int(os.environ.get("DOCTYPE_SAMPLE", "25"))
    seen: Counter[str] = Counter()
    probed = 0
    for rf in forms:
        # Fast server-side TOP query (no full registry scan) for a sample of this form.
        rows = ctx.cosmos.query(
            "99_registry",
            f"SELECT TOP {per_form} c.fnr FROM c WHERE c.status = 'active' AND c.rechtsform = @rf",
            [{"name": "@rf", "value": rf}],
        )
        for row in rows:
            fnr = row.get("fnr")
            if not fnr:
                continue
            try:
                for ref in ctx.source.suche_urkunde(fnr):
                    seen[f"{ref.dokumentart_code}:{ref.dokumentart_text}"] += 1
                probed += 1
            except Exception as exc:  # keep going — this is a best-effort probe
                log.info(
                    "DOCTYPE probe error", extra={"context": {"fnr": fnr, "err": str(exc)[:120]}}
                )
    log.info(
        "DOCTYPE result",
        extra={"context": {"companies_probed": probed, "doctypes": dict(seen)}},
    )


def registry_walk_complete(blob: BlobStoreLike) -> bool:
    """True once the registry grind (``sync-registry``) has finished a full walk.

    Primary signal: sync_registry writes a completion marker when a full seed/reconcile
    finishes (covers the bulk path, which has no walk checkpoint). Fallback for an older
    grind image with no marker: the prefix-walk checkpoint is *cleared* to an empty doc on
    a complete walk (a pending ``frontier`` means it is still running). This lets the
    active-only backfill be scheduled on a cron and start itself the moment the grinder is
    done — no manual hand-off.
    """
    if blob.exists(RAW_CONTAINER, WALK_COMPLETE_MARKER):
        return True
    doc = blob.get_json(RAW_CONTAINER, CHECKPOINT_PATH)
    if doc is None:
        return False  # grind hasn't started / produced a checkpoint yet
    return not doc.get("frontier")  # drained frontier == complete walk


def _sync_overrides() -> tuple[tuple[str, ...], bool]:
    """Read optional sync-registry overrides from env (default = full reconcile)."""
    rfs = os.environ.get("SYNC_RECHTSFORMEN", "").strip()
    rechtsformen = (
        tuple(s.strip() for s in rfs.split(",") if s.strip()) if rfs else DEFAULT_RECHTSFORMEN
    )
    mark_vanished = os.environ.get("SYNC_MARK_VANISHED", "true").strip().lower() != "false"
    return rechtsformen, mark_vanished


def make_run_id(mode: str) -> str:
    """e.g. ``2026-06-16-daily-3f9c1a`` (date + mode + short random suffix)."""
    return f"{now_utc_z()[:10]}-{mode}-{uuid.uuid4().hex[:6]}"


def run(
    mode: str, ctx: PipelineContext, *, run_id: str | None = None, today: date | None = None
) -> int:
    """Run one pipeline pass for *mode*. Returns a process exit code (0 = ok)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")
    run_id = run_id or make_run_id(mode)

    if mode == "diag":
        _run_diag(ctx)  # read-only; no lock needed
        return 0

    if mode == "diag-doctypes":
        _run_doctypes(ctx)  # read-only; no lock needed
        return 0

    if mode == "directories":
        # Monthly: pull the OeNB MFI/NMFI (banks) + EIOPA/GLEIF (insurers) registers, archive
        # verbatim+dated, full-reconcile 00_directories (the register-based FI flag). Both fetches
        # are brittle (esp. the EIOPA SharePoint scrape), so the sync degrades to the last snapshot
        # and ALERTS by email on any failure / sanity-gate trip / refused mass-deactivation. The
        # job exits non-zero on a hard error so Azure surfaces it too. No run-lock needed.
        from fbl_auth.email import email_sender_from_settings
        from fbl_core.config import get_settings
        from fbl_ingest import fetch_eiopa_at, resolve_fns_via_gleif, sync_directories

        settings = get_settings()
        sender = email_sender_from_settings(settings)

        def _alert(subject: str, body: str) -> None:
            try:
                sender.send_alert(settings.alert_email, subject, body)
            except Exception as exc:  # an alert must never crash the run
                log.error("alert email failed", extra={"context": {"error": str(exc)}})

        report = sync_directories(
            ctx.blob,
            ctx.cosmos,
            eiopa_fetch=fetch_eiopa_at,
            gleif=resolve_fns_via_gleif,
            alert=_alert,
        )
        log.info("directories sync", extra={"context": report})
        errors = report.get("errors") or []
        return 1 if errors else 0

    # Lease length (§15a.3, never-stuck): ALL jobs now heartbeat during their long loops
    # (ingest/process between companies; sync-registry between prefix batches), so a uniform
    # SHORT 30-min lease is safe — a live run renews it indefinitely, while a *killed* run's
    # lock self-frees within ~30 min instead of wedging later runs for the old 4h default.
    lock_ttl = 1800
    with run_lock(ctx.cosmos, run_id, ttl_sec=lock_ttl) as acquired:
        if not acquired:
            return 0  # a previous run is still going — exit cleanly (no overlap)

        # Renew the lease during long runs (with the same TTL) so it can't expire mid-run.
        ctx.heartbeat = lambda: heartbeat_run_lock(ctx.cosmos, run_id, ttl_sec=lock_ttl)

        if mode == "sync-registry":
            # Persistent checkpoint → a killed/retried grind resumes instead of restarting.
            # report_blob → archive the "what the change feed missed" drift report (§15a.1).
            # Env overrides (default = full reconcile, so the quarterly job is unchanged):
            #   SYNC_RECHTSFORMEN=AKT,EGE  → sweep only those forms (one-off top-up of a gap)
            #   SYNC_MARK_VANISHED=false   → don't mark unseen FNRs deleted (safe for a partial
            #                                sweep — otherwise a top-up would delete every form
            #                                it didn't sweep).
            rechtsformen, mark_vanished = _sync_overrides()
            sync_registry(
                ctx.source,
                ctx.registry,
                rechtsformen=rechtsformen,
                mark_vanished_deleted=mark_vanished,
                checkpoint=BlobWalkCheckpoint(ctx.blob),
                report_blob=ctx.blob,
                run_id=run_id,
                heartbeat=ctx.heartbeat,  # renew the short lease during the multi-hour walk
            )
            _refresh_stats(ctx)  # quarterly grind reshapes the universe → refresh served stats
            return 0
        if mode == "backfill-ingest":
            # Auto-start guard: do nothing until the registry grind has fully finished, so
            # this job can sit on a cron and kick off by itself once the grinder is done.
            if not registry_walk_complete(ctx.blob):
                log.info("registry walk not complete yet — deferring backfill-ingest")
                return 0
            # Active companies that have master data — bare change-feed stubs are excluded
            # (they stall the bulk grind on unresolvable FNRs; the daily pipeline enriches
            # + ingests them instead). See Registry.ingestable_active_fnrs / §15a.1.
            # Publication-required forms (GmbH/AG …) are filing-checked FIRST, so the per-run
            # time budget closes the real addressable gap before the never-filing tail (ROADMAP
            # P1). Override the order with INGEST_PRIORITY_RECHTSFORMEN (comma list; ""=no
            # priority → pure FNR order).
            priority = tuple(
                f.strip()
                for f in os.environ.get(
                    "INGEST_PRIORITY_RECHTSFORMEN", ",".join(PUBLICATION_PRIORITY_RECHTSFORMEN)
                ).split(",")
                if f.strip()
            )
            fnrs = ctx.registry.ingestable_active_fnrs(priority=priority)
            workers = int(os.environ.get("INGEST_WORKERS", "8"))  # parallelism (latency-bound)
            # Per-run time budget: each scheduled run ends cleanly (checkpoint saved) before
            # the platform could evict it, and the next run resumes the rest. This is what
            # keeps the recurring schedule "never stuck" (§15a.1).
            max_minutes = int(os.environ.get("INGEST_MAX_MINUTES", "50"))
            log.info(
                "backfill-ingest starting",
                extra={
                    "context": {
                        "ingestable": len(fnrs),
                        "priority": list(priority),
                        "workers": workers,
                        "max_minutes": max_minutes,
                    }
                },
            )
            run_ingest(
                ctx.source,
                ctx.registry,
                ctx.blob,
                run_id=run_id,
                fnrs=fnrs,
                include_pdf=False,  # XML only (skip the large PDF siblings)
                checkpoint=BlobIngestCheckpoint(ctx.blob),  # resumable on crash/timeout
                heartbeat=ctx.heartbeat,  # renew the run lock across the multi-hour run
                workers=workers,  # fan out — bottleneck is per-request latency
                max_seconds=max_minutes * 60,  # bounded run → never stuck
            )
            return 0
        if mode == "ingest-fi":
            # FI-targeted PDF ingest (ROADMAP P2.2): banks (BWG) / insurers (VAG) file their
            # Jahresabschluss as a PDF, which the general backfill skips (include_pdf=False) to
            # spare storage across all 340k companies. Here we DO pull the official PDF abschlüsse
            # for the regulated FIs so the MCP's get_document can hand out a signed link to the
            # real document. Its own checkpoint blob keeps this run's done-set separate from the
            # XML-only backfill.
            #
            # The worklist is the UNION of two sources (issue #15): the authoritative OeNB
            # register (00_directories — every licensed bank/Vorsorgekasse, FN-keyed, incl. ones
            # the name heuristic missed like Oberbank), plus the name heuristic — which still
            # carries insurers (VAG), since insurers are NOT in the OeNB MFI/NMFI bank lists and
            # the EIOPA/GLEIF bridge isn't wired yet. Union → no regulated FI is left without its
            # PDF abschluss, and a register bank that was never even in the serving layer gets
            # pulled by FN directly from the HVD API.
            from fbl_ingest import load_fi_directory

            fnrs_heuristic = set(ctx.registry.financial_institution_fnrs())
            fnrs_register = set(load_fi_directory(ctx.cosmos))
            fnrs = sorted(fnrs_heuristic | fnrs_register)
            workers = int(os.environ.get("INGEST_WORKERS", "8"))
            max_minutes = int(os.environ.get("INGEST_MAX_MINUTES", "50"))
            log.info(
                "ingest-fi starting",
                extra={
                    "context": {
                        "financial_institutions": len(fnrs),
                        "from_register": len(fnrs_register),
                        "from_heuristic": len(fnrs_heuristic),
                        "register_only": len(fnrs_register - fnrs_heuristic),
                        "workers": workers,
                        "max_minutes": max_minutes,
                    }
                },
            )
            run_ingest(
                ctx.source,
                ctx.registry,
                ctx.blob,
                run_id=run_id,
                fnrs=fnrs,
                include_pdf=True,  # the whole point — keep the official PDF abschlüsse
                checkpoint=BlobIngestCheckpoint(ctx.blob, path=INGEST_FI_CHECKPOINT_PATH),
                heartbeat=ctx.heartbeat,
                workers=workers,
                max_seconds=max_minutes * 60,
            )
            return 0
        if mode == "backfill-process":
            # GmbH-first bulk processing into layer 10 (raw → consolidated → derived →
            # presented). Hardened: resumable checkpoint, per-run time budget, streaming
            # cohort (§15a.1). Widen to other Rechtsformen by setting PROCESS_RECHTSFORMEN.
            forms = tuple(
                f.strip()
                for f in os.environ.get("PROCESS_RECHTSFORMEN", "GES").split(",")
                if f.strip()
            )
            fnrs = ctx.registry.active_fnrs_by_rechtsform(*forms)
            workers = int(os.environ.get("PROCESS_WORKERS", "8"))  # parallelism (I/O-bound)
            max_minutes = int(os.environ.get("PROCESS_MAX_MINUTES", "50"))
            log.info(
                "backfill-process starting",
                extra={
                    "context": {
                        "forms": list(forms),
                        "companies": len(fnrs),
                        "workers": workers,
                        "max_minutes": max_minutes,
                    }
                },
            )
            process_backfill(
                ctx,
                run_id,
                fnrs,
                checkpoint=BlobProcessCheckpoint(ctx.blob),
                workers=workers,
                max_seconds=max_minutes * 60,
            )
            return 0
        # mode == "daily"
        _run_daily(ctx, run_id, today or _today())
        return 0


def daily_report(ctx: PipelineContext, run_id: str, today: date) -> ProcessReport:
    """Run the daily steady-state pass and return the process report (for tests/metrics)."""
    return _run_daily(ctx, run_id, today)


def _refresh_stats(ctx: PipelineContext, *, with_coverage: bool = True) -> None:
    """Rebuild the materialized ``__stats__`` doc (legal-form/size taxonomy + coverage) that the
    MCP read tools (``list_sectors``/``get_coverage``) serve O(1). Runs after the daily delta
    (sectors only — cheap) and the quarterly grind (with the heavy coverage scan). Best-effort: a
    stats failure must never fail the pipeline run."""
    try:
        from fbl_mcp_server.service import store_stats

        store_stats(ctx.cosmos, include_coverage=with_coverage)
        log.info("stats snapshot refreshed", extra={"context": {"coverage": with_coverage}})
    except Exception as exc:  # materialised view is non-critical; never break the run
        log.warning("stats refresh failed", extra={"context": {"error": str(exc)}})


def _run_daily(ctx: PipelineContext, run_id: str, today: date) -> ProcessReport:
    wm = ctx.registry.get_watermark()
    # Re-check at least `delta_lookback_days` back (overlap catches late feed entries); if the
    # watermark is further back — e.g. the first run after a backfill, where it covers the gap
    # since the raw load, use that. Never start LATER than the floor. The QUARTERLY full grind
    # (sync-registry, 4x/year) is the completeness backstop (§15a.1).
    floor = today - timedelta(days=max(1, ctx.delta_lookback_days))
    von = min(_date_or(wm.last_change_date, floor), floor)

    # 1) Detect changes since the watermark -> marks dirty (+ status flips, new FNRs).
    #    Heartbeat the run lock during detection: against the full register a day's feed can
    #    be tens of thousands of entries, longer than the 30-min lease (§15a.3).
    detect_changes(ctx.source, ctx.registry, von, today, run_id=run_id, heartbeat=ctx.heartbeat)
    # Archive the change-feed responses verbatim before ingest drains the buffer (§5.1).
    archive_raw_responses(ctx.source, ctx.blob, run_id=run_id, prefix="_changes")

    # 2) Partition the dirty set: status-change-only (cheap re-present) vs full rebuild.
    status_only, full = _partition_dirty(ctx)

    # 3) Ingest new raw for the full set, then process it. Heartbeat the lock during ingest:
    #    the dirty set can be thousands of companies (esp. the first run after a backfill), and
    #    downloading their raw takes far longer than the 30-min lease. Without this the lease
    #    expires mid-ingest and process_set then loses the lock and the watermark never advances.
    # Self-bounding: the WHOLE steady-state pass (ingest + process) shares one DAILY_MAX_MINUTES
    # budget (default 5h), then stops cleanly and leaves the rest dirty for the next run. This
    # keeps the run safely under the platform replica-timeout (6h) so a big backlog / catch-up
    # night exits Succeeded and resumes, instead of being hard-killed at the timeout (which is what
    # produced the "Failed" alerts). No data is lost — the dirty set is the checkpoint. Ingest runs
    # parallel (the old serial default was the real long pole on a multi-thousand backlog).
    deadline = time.monotonic() + int(os.environ.get("DAILY_MAX_MINUTES", "300")) * 60
    if full:
        run_ingest(
            ctx.source,
            ctx.registry,
            ctx.blob,
            run_id=run_id,
            fnrs=full,
            heartbeat=ctx.heartbeat,
            workers=int(os.environ.get("INGEST_WORKERS", "8")),
            max_seconds=max(1.0, deadline - time.monotonic()),
        )
    report = process_set(
        ctx,
        run_id,
        full,
        max_seconds=max(1.0, deadline - time.monotonic()),
        today=today.isoformat(),
    )

    # 4) Cheap status refresh for the rest.
    report.status_only_refreshed = refresh_status_only(ctx, run_id, status_only)

    # 5) Advance the watermark to today. The watermark is the CHANGE-FEED read position, not a
    #    work-completion marker: detect_changes (step 1) already read the feed up to `today`, so
    #    that position is reached regardless of how many companies failed downstream. Companies
    #    that failed stay `dirty` and are retried next run (the work queue is separate from the
    #    feed position). Gating this on `failures == 0` meant a single transient failure pinned
    #    the watermark at empty forever, so after a multi-day outage the feed was never
    #    re-queried for the missed days. Decoupling them gives automatic catch-up: after an
    #    outage the watermark sits at the last run date and the next run sweeps the whole gap.
    if report.failures:
        log.warning(
            "daily run had failures; watermark still advanced (feed position), dirty set retries",
            extra={"context": {"failures": report.failures}},
        )
    ctx.registry.set_watermark(today.isoformat())

    # 6) Refresh the served stats snapshot. Sectors only here (fast); the heavy coverage scan
    #    runs on the quarterly grind, keeping the nightly run lean.
    _refresh_stats(ctx, with_coverage=False)
    return report


def _partition_dirty(ctx: PipelineContext) -> tuple[list[str], list[str]]:
    status_only: list[str] = []
    full: list[str] = []
    for fnr in ctx.registry.dirty_fnrs():
        doc = ctx.registry.get(fnr)
        if (
            doc is not None
            and doc.dirty_reason == "status_change"
            and ctx.cosmos.get("10_presentation", fnr) is not None
        ):
            status_only.append(fnr)
        else:
            full.append(fnr)
    return status_only, full


def _today() -> date:
    return date.fromisoformat(now_utc_z()[:10])


def _date_or(value: str | None, fallback: date) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return fallback
    return fallback
