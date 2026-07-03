"""Change-feed delta + raw ingestion tests (§8.3, §15a.2)."""

from __future__ import annotations

from datetime import date

from ingest_fakes import CapturingFakeSource, FakeSource, ja_ref

from fbl_core.storage import RAW_CONTAINER, InMemoryBlobStore, InMemoryCosmosStore
from fbl_firmenbuch_client import DocChange, FirmaChange
from fbl_ingest import detect_changes, run_ingest
from fbl_registry import Registry

VON = date(2026, 6, 16)
BIS = date(2026, 6, 16)


def test_detect_changes_marks_dirty_and_handles_kinds() -> None:
    reg = Registry(InMemoryCosmosStore())
    src = FakeSource(
        firma_changes=[
            FirmaChange(fnr="000001a", art="Neueintragung"),
            FirmaChange(fnr="000002b", art="Löschung"),
            FirmaChange(fnr="000003c", art="Änderung"),
        ],
        doc_changes=[DocChange(key="000004d_x_PDF", fnr="000004d", dokumentart_code="48")],
    )
    report = detect_changes(src, reg, VON, BIS, run_id="r1")
    assert report.new_companies == 1
    assert report.status_changes == 1
    assert report.doc_changes == 1
    assert set(report.dirty_fnrs) == {"000001a", "000002b", "000003c", "000004d"}
    # Löschung flips status to deleted but still marks dirty (cheap re-present)
    deleted = reg.get("000002b")
    assert deleted is not None and deleted.status == "deleted"
    assert deleted.dirty_reason == "status_change"


def test_date_windows_slices_to_at_most_7_days() -> None:
    # The change-feed API rejects windows > 7 days; a catch-up must be sliced.
    from datetime import timedelta

    from fbl_ingest.delta import _date_windows

    wins = list(_date_windows(date(2026, 6, 10), date(2026, 6, 24)))  # 15-day catch-up
    assert all((b - a).days <= 6 for a, b in wins)  # every window ≤ 7 calendar days
    assert wins[0][0] == date(2026, 6, 10) and wins[-1][1] == date(2026, 6, 24)
    from itertools import pairwise

    for prev, nxt in pairwise(wins):
        assert nxt[0] == prev[1] + timedelta(days=1)  # contiguous, no gaps/overlaps
    # A normal daily range stays a single window.
    assert list(_date_windows(date(2026, 6, 16), date(2026, 6, 16))) == [
        (date(2026, 6, 16), date(2026, 6, 16))
    ]


def test_detect_changes_chunks_long_window_into_feed_calls() -> None:
    # A 10-day catch-up must produce multiple feed calls, none spanning > 7 days.
    reg = Registry(InMemoryCosmosStore())
    src = CapturingFakeSource()
    detect_changes(
        src, reg, date(2026, 6, 14), date(2026, 6, 24), run_id="r", rechtsformen=("GES",)
    )
    # firma feed: 2 windows for one Rechtsform; each window's span ≤ 7 days.
    assert len(src.firma_calls) == 2
    assert all((bis - von).days <= 6 for von, bis in src.firma_calls)


def test_run_ingest_downloads_and_is_idempotent() -> None:
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    reg.ensure("030435h", source="x")
    src = FakeSource(
        universe={"030435h": "Westerthaler GmbH"},
        documents={
            "030435h": [
                ja_ref("030435h", "2024-12-31", "xml"),
                ja_ref("030435h", "2024-12-31", "pdf"),
                ja_ref("030435h", "2023-12-31", "xml"),
            ]
        },
    )
    report = run_ingest(src, reg, blob, run_id="r1", fnrs=["030435h"])
    assert report.filings_downloaded == 2  # two XML
    assert report.pdfs_downloaded == 1  # one PDF sibling
    assert report.failures == 0

    # raw artifacts + manifest present (filenames carry a doc-key token, §15b-11)
    paths = blob.list_paths(RAW_CONTAINER, "030435h/2024-12-31/")
    assert any(p.endswith(".xml") for p in paths)
    assert any(p.endswith(".pdf") for p in paths)
    manifest = blob.get_json(RAW_CONTAINER, "030435h/2024-12-31/_manifest.json")
    assert manifest is not None and len(manifest["artifacts"]) == 2
    assert manifest["_meta"]["stage"] == "raw"
    # master auszug archived (§5.1)
    assert any(p.startswith("030435h/master/auszug_") for p in blob._data[RAW_CONTAINER])

    # known_filings recorded
    doc = reg.get("030435h")
    assert doc is not None and len(doc.known_filings) == 3

    # Re-run: everything already known -> nothing re-downloaded (idempotent).
    calls_before = len(src.urkunde_calls)
    report2 = run_ingest(src, reg, blob, run_id="r2", fnrs=["030435h"])
    assert report2.filings_downloaded == 0 and report2.pdfs_downloaded == 0
    assert report2.filings_skipped == 3
    assert len(src.urkunde_calls) == calls_before  # no new downloads


def test_run_ingest_archives_verbatim_responses() -> None:
    # §5.1: a capturing source's sucheUrkunde + auszug responses are archived
    # byte-for-byte under 90-raw/{fnr}/_responses/{run_id}/.
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    reg.ensure("030435h", source="x")
    src = CapturingFakeSource(
        universe={"030435h": "Westerthaler GmbH"},
        documents={"030435h": [ja_ref("030435h", "2024-12-31", "xml")]},
    )
    report = run_ingest(src, reg, blob, run_id="r1", fnrs=["030435h"])
    assert report.responses_archived == 2  # sucheUrkunde + auszug (urkunde excluded)

    resp_paths = blob.list_paths(RAW_CONTAINER, "030435h/_responses/r1/")
    assert any(p.endswith("_sucheUrkunde.xml") for p in resp_paths)
    assert any(p.endswith("_auszug_v2.xml") for p in resp_paths)
    # archived bytes are verbatim
    body = blob.get_bytes(RAW_CONTAINER, next(p for p in resp_paths if "auszug" in p))
    assert body == b"<AUSZUG fnr='030435h'/>"


def test_run_ingest_archival_is_noop_for_non_capturing_source() -> None:
    # A plain FakeSource (no drain_raw) must not break ingest and archives nothing.
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    reg.ensure("030435h", source="x")
    src = FakeSource(documents={"030435h": [ja_ref("030435h", "2024-12-31", "xml")]})
    report = run_ingest(src, reg, blob, run_id="r1", fnrs=["030435h"])
    assert report.responses_archived == 0
    assert blob.list_paths(RAW_CONTAINER, "030435h/_responses/") == []


def test_run_ingest_dead_letters_on_error() -> None:
    reg = Registry(InMemoryCosmosStore())
    blob = InMemoryBlobStore()
    reg.ensure("999999z", source="x")

    class BoomSource(FakeSource):
        def suche_urkunde(self, fnr):  # type: ignore[no-untyped-def]
            from fbl_firmenbuch_client import FirmenbuchApiError

            raise FirmenbuchApiError("boom", endpoint="sucheUrkunde")

    report = run_ingest(BoomSource(), reg, blob, run_id="r1", fnrs=["999999z"])
    assert report.failures == 1 and report.dead_letters == ["999999z"]
    doc = reg.get("999999z")
    assert doc is not None and doc.pipeline_state == "failed"
