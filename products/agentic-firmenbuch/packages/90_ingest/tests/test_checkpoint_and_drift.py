"""Persistent walk checkpoint (resume) + the change-feed-missed drift report (§15a.1)."""

from __future__ import annotations

import pytest
from ingest_fakes import FakeSource

from fbl_core.storage import RAW_CONTAINER, InMemoryBlobStore, InMemoryCosmosStore
from fbl_firmenbuch_client import FirmaResult
from fbl_ingest import (
    BlobWalkCheckpoint,
    InMemoryCheckpoint,
    WalkState,
    prefix_walk,
    sync_registry,
)
from fbl_registry import Registry

# --- checkpoint ------------------------------------------------------------------------


def test_blob_checkpoint_roundtrip() -> None:
    blob = InMemoryBlobStore()
    cp = BlobWalkCheckpoint(blob)
    assert cp.load() is None  # nothing persisted yet
    state = WalkState(
        seen={"000001a": FirmaResult(fnr="000001a")},
        done={"GES|a", "GES|b"},
        incomplete=["GES|immobilienxxxxxxxx"],
        frontier=[("GES", "ab"), ("AKT", "")],
        counts_by_rechtsform={"GES": 1},
    )
    cp.save(state)
    got = cp.load()
    assert got is not None
    assert got.done == {"GES|a", "GES|b"}
    assert got.incomplete == ["GES|immobilienxxxxxxxx"]
    assert got.frontier == [("GES", "ab"), ("AKT", "")]
    assert got.counts_by_rechtsform == {"GES": 1}
    assert set(got.seen) == {"000001a"}  # rebuilt as a placeholder FirmaResult (keys only)
    cp.clear()
    assert cp.load() is None


def test_completed_walk_clears_blob_checkpoint() -> None:
    blob = InMemoryBlobStore()
    cp = BlobWalkCheckpoint(blob)
    src = FakeSource(universe={"000001a": "Alpha", "000002b": "Beta"})
    res = prefix_walk(src, rechtsformen=("GES",), checkpoint=cp)
    assert set(res.found) == {"000001a", "000002b"}
    assert cp.load() is None  # cleared on completion


def test_checkpoint_resume_after_crash() -> None:
    # A crash mid-walk persists progress; resume completes WITHOUT losing companies.
    universe = {
        "000001a": "Alpha",
        "000002b": "Alfa",
        "000003c": "Apple",
        "000004d": "Banana",
    }
    ckpt = InMemoryCheckpoint()

    class CrashAfter(FakeSource):
        def suche_firma(self, firmenwortlaut, **kw):  # type: ignore[no-untyped-def]
            if self.suche_firma_calls >= 2:
                raise RuntimeError("simulated crash")
            return super().suche_firma(firmenwortlaut, **kw)

    with pytest.raises(RuntimeError):
        prefix_walk(
            CrashAfter(universe=universe),
            rechtsformen=("GES",),
            cap=2,
            checkpoint=ckpt,
            save_every=1,
        )
    mid = ckpt.load()
    assert mid is not None and mid.done  # progress persisted before the crash

    # Resume with a healthy source: the walk finishes and finds every company.
    res = prefix_walk(
        FakeSource(universe=universe), rechtsformen=("GES",), cap=2, checkpoint=ckpt, save_every=1
    )
    assert set(res.found) == set(universe)
    assert ckpt.load() is None  # completed -> cleared


# --- drift report ----------------------------------------------------------------------


def test_drift_report_initial_seed_omits_company_list() -> None:
    reg = Registry(InMemoryCosmosStore())  # empty -> initial seed
    blob = InMemoryBlobStore()
    src = FakeSource(universe={"000001a": "Alpha", "000002b": "Beta"})
    rep = sync_registry(src, reg, rechtsformen=("GES",), report_blob=blob, run_id="seed1")
    assert rep.was_initial_seed is True
    assert rep.seeded == 2
    assert rep.seeded_companies == []  # not real drift on the first run -> omitted
    doc = blob.get_json(RAW_CONTAINER, "_reports/sync-registry/seed1.json")
    assert doc is not None and doc["was_initial_seed"] is True


def test_drift_report_reconcile_flags_what_change_feed_missed() -> None:
    reg = Registry(InMemoryCosmosStore())
    # Pretend the change feed had already populated these two (so it's a reconcile, not a seed).
    reg.ensure("000001a", source="veraenderungenFirma", name="Alpha")
    reg.ensure("000003c", source="veraenderungenFirma", name="Gamma")
    blob = InMemoryBlobStore()
    # Authoritative sweep: Alpha still there, Beta is NEW, Gamma has VANISHED.
    src = FakeSource(universe={"000001a": "Alpha", "000002b": "Beta"})
    rep = sync_registry(src, reg, rechtsformen=("GES",), report_blob=blob, run_id="recon1")

    assert rep.was_initial_seed is False
    assert [e.fnr for e in rep.seeded_companies] == ["000002b"]  # Neueintragung the feed missed
    assert [e.fnr for e in rep.deleted_companies] == ["000003c"]  # Löschung the feed missed

    doc = blob.get_json(RAW_CONTAINER, "_reports/sync-registry/recon1.json")
    assert doc is not None
    assert doc["seeded_companies"][0]["fnr"] == "000002b"
    assert doc["deleted_companies"][0]["fnr"] == "000003c"
