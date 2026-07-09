"""End-to-end orchestration tests on in-memory stores (§8.8 DoD)."""

from __future__ import annotations

from datetime import date

from orch_fakes import FakeSource, ja_ref

from fbl_core.lineage import content_hash
from fbl_core.storage import InMemoryBlobStore, InMemoryCosmosStore
from fbl_orchestration import PipelineContext, daily_report, run
from fbl_orchestration.runlock import (
    RUN_LOCK_ID,
    acquire_run_lock,
    heartbeat_run_lock,
)
from fbl_registry import Registry


def _ctx(source: FakeSource) -> PipelineContext:
    cosmos = InMemoryCosmosStore()
    return PipelineContext(
        blob=InMemoryBlobStore(),
        cosmos=cosmos,
        source=source,
        registry=Registry(cosmos),
        current_year=2026,
    )


def _source_two_years() -> FakeSource:
    return FakeSource(
        universe={"030435h": "Westerthaler GmbH"},
        documents={"030435h": [ja_ref("030435h", "2023-12-31"), ja_ref("030435h", "2024-12-31")]},
        values={"2023-12-31": (1000.0, 600.0), "2024-12-31": (1200.0, 700.0)},
    )


def test_initial_load_end_to_end() -> None:
    ctx = _ctx(_source_two_years())
    assert run("sync-registry", ctx) == 0
    assert ctx.registry.all_fnrs() == ["030435h"]

    assert run("backfill-ingest", ctx) == 0
    assert any(p.endswith(".xml") for p in ctx.blob.list_paths("90-raw", "030435h/2024-12-31/"))

    assert run("backfill-process", ctx) == 0
    presented = ctx.cosmos.get("10_presentation", "030435h")
    assert presented is not None
    assert presented["financials"]["latest"]["bilanzsumme"] == 1200.0
    assert presented["identity"]["status"] == "active"
    assert presented["size"]["gkl"] == "K"
    # consolidated + derived also written
    assert ctx.cosmos.get("50_consolidated", "030435h") is not None
    assert ctx.cosmos.get("30_derived", "030435h") is not None
    # registry marked clean
    assert ctx.registry.dirty_fnrs() == []


def test_ingest_fi_pulls_only_financial_institutions() -> None:
    # ROADMAP P2.2: the FI-targeted PDF ingest selects banks/insurers (here by name) and pulls
    # their official filing, while leaving ordinary companies untouched (storage guard).
    src = FakeSource(
        universe={
            "012345f": "Volksbank Niederösterreich AG",  # bank (name match) → FI
            "030435h": "Westerthaler GmbH",  # ordinary company → not an FI
        },
        documents={
            "012345f": [ja_ref("012345f", "2024-12-31", ext="pdf")],
            "030435h": [ja_ref("030435h", "2024-12-31")],
        },
        values={"2024-12-31": (1200.0, 700.0)},
    )
    ctx = _ctx(src)
    run("sync-registry", ctx)
    assert ctx.registry.financial_institution_fnrs() == ["012345f"]

    assert run("ingest-fi", ctx) == 0
    assert ctx.blob.list_paths("90-raw", "012345f/2024-12-31/")  # FI filing ingested
    assert ctx.blob.list_paths("90-raw", "030435h/") == []  # ordinary company untouched
    # Its own checkpoint blob, kept separate from the XML-only backfill's done-set.
    assert ctx.blob.get_json("90-raw", "_checkpoints/ingest_fi.json") is not None


def test_backfill_process_is_idempotent() -> None:
    ctx = _ctx(_source_two_years())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)
    first = ctx.cosmos.get("10_presentation", "030435h")
    run("backfill-process", ctx)
    second = ctx.cosmos.get("10_presentation", "030435h")
    assert first is not None and second is not None
    assert content_hash(first) == content_hash(second)  # no change on re-run


def test_refresh_stats_mode_rebuilds_the_stats_doc_with_coverage() -> None:
    # Issue #12: the weekly refresh-stats mode materialises the full __stats__ snapshot.
    ctx = _ctx(_source_two_years())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)
    assert run("refresh-stats", ctx) == 0
    stats = ctx.cosmos.get("10_presentation", "__stats__")
    assert stats is not None
    assert "coverage" in stats["stats"] and "sectors" in stats["stats"]


def test_run_lock_prevents_overlap() -> None:
    ctx = _ctx(_source_two_years())
    # Simulate a previous run holding the lock.
    assert acquire_run_lock(ctx.cosmos, "other-run") is True
    # A new daily run finds the lock held and exits 0 without processing.
    assert run("daily", ctx) == 0
    assert ctx.cosmos.get("10_presentation", "030435h") is None  # nothing processed


def test_heartbeat_extends_lease_and_detects_loss() -> None:
    ctx = _ctx(_source_two_years())
    assert acquire_run_lock(ctx.cosmos, "run-A", ttl_sec=100) is True
    before = ctx.cosmos.get("99_registry", RUN_LOCK_ID)
    assert before is not None
    # The owning run renews its lease (expiry pushed out, heartbeat stamped).
    assert heartbeat_run_lock(ctx.cosmos, "run-A", ttl_sec=14400) is True
    after = ctx.cosmos.get("99_registry", RUN_LOCK_ID)
    assert after is not None
    assert after["expires_at"] > before["expires_at"]
    assert "heartbeat_at" in after
    # A different run cannot renew a lease it does not own (signals it was overtaken).
    assert heartbeat_run_lock(ctx.cosmos, "run-B") is False


def test_daily_picks_up_new_filing_and_advances_watermark() -> None:
    from fbl_firmenbuch_client import DocChange

    src = _source_two_years()
    ctx = _ctx(src)
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)

    # A new filing for 2025 arrives via the change feed.
    src.documents["030435h"].append(ja_ref("030435h", "2025-12-31"))
    src.values["2025-12-31"] = (1500.0, 900.0)
    src.doc_changes = [DocChange(key="030435_x_XML", fnr="030435h", dokumentart_code="48")]

    report = daily_report(ctx, "2026-06-16-daily-x", date(2026, 6, 16))
    assert report.processed == 1
    presented = ctx.cosmos.get("10_presentation", "030435h")
    assert presented is not None
    assert presented["financials"]["latest"]["bilanzsumme"] == 1500.0  # new year reflected
    assert ctx.registry.get_watermark().last_change_date == "2026-06-16"


def test_daily_advances_watermark_even_when_a_company_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The watermark is the change-feed read position, not a work-completion marker: a
    downstream per-company failure must NOT pin it (otherwise the feed is never re-queried
    after an outage and changes are silently missed). Failed companies stay dirty and retry."""
    from fbl_firmenbuch_client import DocChange
    from fbl_orchestration import orchestrator
    from fbl_orchestration.pipeline import ProcessReport

    src = _source_two_years()
    ctx = _ctx(src)
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)

    src.doc_changes = [DocChange(key="030435_x_XML", fnr="030435h", dokumentart_code="48")]

    def _failing(ctx_: object, run_id: str, fnrs: list[str], **_kw: object) -> ProcessReport:
        return ProcessReport(run_id=run_id, processed=0, failures=1)  # something failed

    monkeypatch.setattr(orchestrator, "process_set", _failing)
    daily_report(ctx, "2026-06-16-daily-f", date(2026, 6, 16))
    # Despite the failure, the feed position advanced.
    assert ctx.registry.get_watermark().last_change_date == "2026-06-16"
    # ...and the company is still dirty, so it will be retried next run (nothing dropped).
    assert "030435h" in ctx.registry.dirty_fnrs()


def test_daily_advances_watermark_before_processing_can_be_killed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: in prod the watermark froze because it was written at the END of the daily
    run, after an unbounded status refresh that the platform replica-timeout SIGKILLed. It must
    be advanced right after detect_changes so even a hard crash mid-processing can't strand it —
    otherwise every later run re-scans weeks of feed (von = the stale watermark) and the change
    set outgrows the time budget forever."""
    import pytest

    from fbl_firmenbuch_client import DocChange
    from fbl_orchestration import orchestrator

    src = _source_two_years()
    ctx = _ctx(src)
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)

    src.doc_changes = [DocChange(key="030435_x_XML", fnr="030435h", dokumentart_code="48")]

    def _killed(*_a: object, **_k: object) -> object:  # simulate replica timeout / OOM kill
        raise RuntimeError("replica killed mid-processing")

    monkeypatch.setattr(orchestrator, "process_set", _killed)
    with pytest.raises(RuntimeError):
        daily_report(ctx, "2026-06-16-daily-k", date(2026, 6, 16))
    # The feed position was persisted BEFORE the kill point, so the next run resumes from today
    # (3-day lookback) instead of re-scanning the whole gap since the last successful run.
    assert ctx.registry.get_watermark().last_change_date == "2026-06-16"


def test_daily_lookback_floor_widens_change_window() -> None:
    # With no/recent watermark, a daily run re-checks `delta_lookback_days` back — the overlap
    # that catches late feed entries and, set high, the one-time catch-up after a backfill.
    src = _source_two_years()
    ctx = _ctx(src)
    ctx.delta_lookback_days = 10
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)

    src.queried_von.clear()
    daily_report(ctx, "2026-06-23-daily-z", date(2026, 6, 23))
    # von floored at today - 10 days (2026-06-13), not the default today - 1.
    assert min(src.queried_von) == date(2026, 6, 13)


def test_daily_status_change_refreshes_present_only() -> None:
    from fbl_firmenbuch_client import FirmaChange

    src = _source_two_years()
    ctx = _ctx(src)
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    run("backfill-process", ctx)

    # A Löschung arrives: status -> deleted, no new filing.
    src.firma_changes = [FirmaChange(fnr="030435h", art="Löschung")]
    report = daily_report(ctx, "2026-06-17-daily-y", date(2026, 6, 17))
    assert report.status_only_refreshed == 1
    assert report.processed == 0  # no full rebuild
    presented = ctx.cosmos.get("10_presentation", "030435h")
    assert presented is not None and presented["identity"]["status"] == "deleted"


def _two_ges_companies() -> FakeSource:
    return FakeSource(
        universe={"0001a": "Alpha GmbH", "0002b": "Beta GmbH"},
        documents={
            "0001a": [ja_ref("0001a", "2024-12-31")],
            "0002b": [ja_ref("0002b", "2024-12-31")],
        },
        values={"2024-12-31": (1000.0, 600.0)},
    )


def test_process_set_is_time_bounded_and_resumable() -> None:
    # The daily run self-bounds: when the budget is spent it stops and leaves the rest dirty,
    # so the platform timeout can't hard-kill it mid-company and nothing is lost.
    from fbl_orchestration.pipeline import process_set

    ctx = _ctx(_two_ges_companies())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)  # raw now in blob for both
    fnrs = ["0001a", "0002b"]

    # Budget already blown (clock jumps past the deadline) → nothing presented this pass.
    ticks = iter([0.0, 100.0, 100.0, 100.0])
    report = process_set(ctx, "r-budget", fnrs, max_seconds=50.0, clock=lambda: next(ticks))
    assert report.processed == 0
    assert ctx.cosmos.get("10_presentation", "0001a") is None

    # No budget → resumes and finishes both (nothing lost).
    report2 = process_set(ctx, "r-full", fnrs)
    assert report2.processed == 2
    assert ctx.cosmos.get("10_presentation", "0001a") is not None
    assert ctx.cosmos.get("10_presentation", "0002b") is not None


def test_process_backfill_is_bounded_and_resumable() -> None:
    from fbl_ingest import BlobProcessCheckpoint
    from fbl_orchestration.pipeline import process_backfill

    ctx = _ctx(_two_ges_companies())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)  # raw XML now in 90-raw for both
    fnrs = ctx.registry.active_fnrs_by_rechtsform("GES")
    assert fnrs == ["0001a", "0002b"]
    cp = BlobProcessCheckpoint(ctx.blob)

    # First call: time budget trips after the first consolidation → bounded, partial, saved.
    ticks = iter([0.0, 100.0, 100.0, 100.0])
    process_backfill(
        ctx, "p1", fnrs, checkpoint=cp, save_every=1, max_seconds=50.0, clock=lambda: next(ticks)
    )
    cons_done, pres_done = cp.load()
    assert cons_done == {"0001a"}  # stopped mid-Phase-A, only one consolidated
    assert pres_done == set()  # gate: derive doesn't start until ALL are consolidated
    assert ctx.cosmos.get("10_presentation", "0001a") is None

    # Second call: no budget → finishes consolidation, builds cohort, presents both.
    process_backfill(ctx, "p2", fnrs, checkpoint=cp)
    cons_done, pres_done = cp.load()
    assert cons_done == {"0001a", "0002b"}
    assert pres_done == {"0001a", "0002b"}
    assert ctx.cosmos.get("10_presentation", "0001a") is not None
    assert ctx.cosmos.get("10_presentation", "0002b") is not None


def test_process_backfill_reprocesses_reingested_dirty_company() -> None:
    # Issue #7: a company re-ingested (→ marked dirty) AFTER it was already "done" in the
    # process checkpoint must be rebuilt from the new raw, not skipped as done.
    from fbl_ingest import BlobProcessCheckpoint
    from fbl_orchestration.pipeline import process_backfill

    ctx = _ctx(_two_ges_companies())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    fnrs = ctx.registry.active_fnrs_by_rechtsform("GES")
    cp = BlobProcessCheckpoint(ctx.blob)
    process_backfill(ctx, "p1", fnrs, checkpoint=cp)
    cons_done, pres_done = cp.load()
    assert cons_done == {"0001a", "0002b"} and pres_done == {"0001a", "0002b"}
    assert ctx.registry.dirty_fnrs() == []  # both clean after present

    # Simulate a re-ingest of NEW raw for one company (what record_filing does) → it goes dirty.
    ctx.registry.mark_dirty("0001a", reason="new_filing")
    assert ctx.registry.dirty_fnrs() == ["0001a"]

    # Second backfill-process: it must rebuild ONLY the dirty company (evicted from the
    # checkpoint), skip the other (still done), and clear the dirty flag via present's mark_clean.
    report = process_backfill(ctx, "p2", fnrs, checkpoint=cp)
    assert report.processed == 1  # only the re-ingested company rebuilt; the clean one skipped
    assert ctx.registry.dirty_fnrs() == []  # 0001a reprocessed → clean again, self-resolving


def test_process_backfill_parallel_processes_all() -> None:
    from fbl_ingest import BlobProcessCheckpoint
    from fbl_orchestration.pipeline import process_backfill

    ctx = _ctx(_two_ges_companies())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)
    fnrs = ctx.registry.active_fnrs_by_rechtsform("GES")
    cp = BlobProcessCheckpoint(ctx.blob)
    report = process_backfill(ctx, "par", fnrs, checkpoint=cp, workers=4)
    cons_done, pres_done = cp.load()
    assert cons_done == {"0001a", "0002b"} and pres_done == {"0001a", "0002b"}
    assert report.processed == 2
    assert ctx.cosmos.get("10_presentation", "0001a") is not None
    assert ctx.cosmos.get("10_presentation", "0002b") is not None


def test_parse_all_writes_through_70_parsed_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """parse_all persists each filing to 70-parsed and reuses it on re-runs (no re-parse)."""
    from fbl_core.storage import PARSED_CONTAINER
    from fbl_orchestration import loaders

    ctx = _ctx(_source_two_years())
    run("sync-registry", ctx)
    run("backfill-ingest", ctx)

    first = loaders.parse_all(ctx.blob, "030435h")
    assert len(first) == 2  # 2023 + 2024 XML filings
    cached = ctx.blob.list_paths(PARSED_CONTAINER, "030435h/")
    assert sum(p.endswith(".json") for p in cached) == 2  # both persisted to 70-parsed

    # Second call must hit the cache — parse_filing must NOT be invoked again.
    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("re-parsed despite a valid 70-parsed cache entry")

    monkeypatch.setattr(loaders, "parse_filing", _boom)
    second = loaders.parse_all(ctx.blob, "030435h")
    assert [f.model_dump(mode="json") for f in second] == [f.model_dump(mode="json") for f in first]
