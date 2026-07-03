"""Active-only + XML-only + resumable backfill-ingest (the "second run")."""

from __future__ import annotations

from ingest_fakes import FakeSource, ja_ref

from fbl_core.storage import RAW_CONTAINER, InMemoryBlobStore, InMemoryCosmosStore
from fbl_ingest import BlobIngestCheckpoint, run_ingest
from fbl_registry import Registry


def _registry_with_statuses() -> Registry:
    reg = Registry(InMemoryCosmosStore())
    reg.ensure("0001a", source="t", status="active", name="Active One")
    reg.ensure("0002b", source="t", status="active", name="Active Two")
    reg.ensure("0003c", source="t", status="historical", name="Old One")
    reg.ensure("0004d", source="t", status="deleted", name="Gone One")
    return reg


def test_active_fnrs_excludes_historical_and_deleted() -> None:
    reg = _registry_with_statuses()
    assert reg.active_fnrs() == ["0001a", "0002b"]
    assert reg.all_fnrs() == ["0001a", "0002b", "0003c", "0004d"]


def test_xml_only_never_downloads_the_pdf_sibling() -> None:
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    src = FakeSource(
        universe={"0001a": "Active One"},
        documents={
            "0001a": [
                ja_ref("0001a", "2023-12-31", "xml"),
                ja_ref("0001a", "2023-12-31", "pdf"),  # must be filtered out pre-download
            ]
        },
    )
    report = run_ingest(src, reg, blob, run_id="r1", fnrs=["0001a"], include_pdf=False)

    # The PDF key was never even fetched, and nothing PDF landed in the blob store.
    assert src.urkunde_calls == ["0001a|2023-12-31|xml"]
    assert report.pdfs_downloaded == 0
    assert report.filings_downloaded == 1
    assert not any(p.endswith(".pdf") for p in blob.list_paths(RAW_CONTAINER))


def test_checkpoint_resumes_and_skips_completed_companies() -> None:
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    src = FakeSource(
        universe={"0001a": "One", "0002b": "Two"},
        documents={
            "0001a": [ja_ref("0001a", "2023-12-31", "xml")],
            "0002b": [ja_ref("0002b", "2023-12-31", "xml")],
        },
    )
    cp = BlobIngestCheckpoint(blob)
    cp.save_done({"0001a"})  # pretend 0001a finished before a crash

    report = run_ingest(
        src, reg, blob, run_id="r2", fnrs=["0001a", "0002b"], include_pdf=False, checkpoint=cp
    )

    # Only the not-yet-done company is processed on resume.
    assert report.companies == 1
    assert src.urkunde_calls == ["0002b|2023-12-31|xml"]
    # Both are now recorded as done for the next restart.
    assert cp.load_done() == {"0001a", "0002b"}


def test_heartbeat_loss_stops_the_run() -> None:
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    src = FakeSource(
        universe={"0001a": "One", "0002b": "Two"},
        documents={
            "0001a": [ja_ref("0001a", "2023-12-31", "xml")],
            "0002b": [ja_ref("0002b", "2023-12-31", "xml")],
        },
    )
    report = run_ingest(
        src,
        reg,
        blob,
        run_id="r3",
        fnrs=["0001a", "0002b"],
        include_pdf=False,
        heartbeat=lambda: False,  # lock lost immediately after the first company
    )
    assert report.companies == 1  # stopped before the second


def test_parallel_workers_process_every_company() -> None:
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    fnrs = [f"{i:04d}x" for i in range(12)]
    src = FakeSource(
        universe={f: f"Co {f}" for f in fnrs},
        documents={f: [ja_ref(f, "2023-12-31", "xml")] for f in fnrs},
    )
    cp = BlobIngestCheckpoint(blob)
    report = run_ingest(
        src, reg, blob, run_id="par", fnrs=fnrs, include_pdf=False, checkpoint=cp, workers=4
    )
    assert report.companies == 12
    assert cp.load_done() == set(fnrs)  # all processed, checkpoint durable
    assert report.filings_downloaded == 12  # one XML each, fetched concurrently


def test_max_seconds_stops_run_cleanly_with_checkpoint() -> None:
    """A per-run time budget ends the run after the current company, with progress saved —
    the next run resumes the rest. This is what keeps the recurring schedule never-stuck."""
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    fnrs = ["0001a", "0002b", "0003c"]
    src = FakeSource(
        universe={f: f"Co {f}" for f in fnrs},
        documents={f: [ja_ref(f, "2023-12-31", "xml")] for f in fnrs},
    )
    cp = BlobIngestCheckpoint(blob)
    # Fake monotonic clock: deadline = first_tick + 50 = 50; the check after company #1
    # reads 100 ≥ 50 → stop. (sequential path; save_every=1 persists each company)
    ticks = iter([0.0, 100.0, 200.0, 300.0])
    report = run_ingest(
        src,
        reg,
        blob,
        run_id="budget",
        fnrs=fnrs,
        include_pdf=False,
        checkpoint=cp,
        save_every=1,
        max_seconds=50.0,
        clock=lambda: next(ticks),
    )
    assert report.companies == 1  # stopped after the first, did not grind the rest
    assert cp.load_done() == {"0001a"}  # progress persisted → resumable next run
