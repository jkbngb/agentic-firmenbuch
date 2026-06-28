"""Registry catalog tests (§15a.0)."""

from __future__ import annotations

from fbl_core.storage import InMemoryCosmosStore
from fbl_registry import KnownFiling, Registry


def _registry() -> Registry:
    return Registry(InMemoryCosmosStore())


def test_ensure_is_idempotent() -> None:
    reg = _registry()
    a = reg.ensure("030435h", source="sucheFirma_sweep")
    b = reg.ensure("030435h", source="veraenderungenFirma")
    assert a.fnr == b.fnr == "030435h"
    assert reg.count() == 1
    assert a.source == "sucheFirma_sweep"  # not overwritten on re-ensure


def test_active_fnrs_by_rechtsform_filters_to_form() -> None:
    reg = _registry()
    reg.ensure("0001a", source="t", name="A GmbH", rechtsform="GES")
    reg.ensure("0002b", source="t", name="B GmbH", rechtsform="GES")
    reg.ensure("0003c", source="t", name="C AG", rechtsform="AG")
    reg.ensure("0004d", source="t", status="historical", name="D GmbH", rechtsform="GES")
    assert reg.active_fnrs_by_rechtsform("GES") == ["0001a", "0002b"]  # active GES only
    assert reg.active_fnrs_by_rechtsform("GES", "AG") == ["0001a", "0002b", "0003c"]


def test_ingestable_active_fnrs_excludes_bare_change_feed_stubs() -> None:
    reg = _registry()
    # A walked company (has master data) and a deleted one.
    reg.ensure("030435h", source="sucheFirma_sweep", name="Muster GmbH", rechtsform="GES")
    reg.ensure("099999z", status="historical", source="sucheFirma_sweep", name="Alt GmbH")
    # A bare change-feed stub: only an FNR, no name/rechtsform yet.
    reg.ensure("423155m", source="veraenderungenFirma")

    # active_fnrs still returns every active FNR (incl. the stub) — used elsewhere.
    assert set(reg.active_fnrs()) == {"030435h", "423155m"}
    # the backfill worklist excludes the nameless stub (it would stall the bulk grind).
    assert reg.ingestable_active_fnrs() == ["030435h"]


def test_ingestable_active_fnrs_prioritises_publication_forms() -> None:
    reg = _registry()
    # Mixed forms; FNRs chosen so plain sort would interleave them.
    reg.ensure("01ou", source="t", name="O OG", rechtsform="OG")  # tail (never files)
    reg.ensure("02ag", source="t", name="A AG", rechtsform="AG")  # priority #2
    reg.ensure("03eu", source="t", name="E e.U.", rechtsform="EU")  # tail
    reg.ensure("04ge", source="t", name="G GmbH", rechtsform="GES")  # priority #1
    reg.ensure("05ge", source="t", name="H GmbH", rechtsform="GES")  # priority #1

    # No priority → pure FNR order (unchanged behaviour, backward-compatible).
    assert reg.ingestable_active_fnrs() == ["01ou", "02ag", "03eu", "04ge", "05ge"]

    # With a priority list: GES before AG before the unlisted tail; FNR-sorted within a tier.
    assert reg.ingestable_active_fnrs(priority=("GES", "AG")) == [
        "04ge",
        "05ge",
        "02ag",
        "01ou",
        "03eu",
    ]


def test_dirty_clean_lifecycle() -> None:
    reg = _registry()
    reg.ensure("030435h", source="x")
    reg.ensure("093450b", source="x")
    reg.mark_dirty("030435h", reason="new_filing")
    assert reg.dirty_fnrs() == ["030435h"]
    reg.mark_clean("030435h")
    assert reg.dirty_fnrs() == []


def test_dead_letter() -> None:
    reg = _registry()
    reg.ensure("030435h", source="x")
    reg.dead_letter("030435h", "boom")
    doc = reg.get("030435h")
    assert doc is not None and doc.pipeline_state == "failed" and doc.dead_letter == "boom"


def test_record_filing_dedup_by_doc_key() -> None:
    reg = _registry()
    reg.ensure("030435h", source="x")
    reg.record_filing("030435h", KnownFiling(stichtag="2024-12-31", doc_key="K1", downloaded=True))
    reg.record_filing("030435h", KnownFiling(stichtag="2024-12-31", doc_key="K1", downloaded=True))
    doc = reg.get("030435h")
    assert doc is not None and len(doc.known_filings) == 1
    assert reg.has_filing("030435h", "K1")
    assert not reg.has_filing("030435h", "K2")


def test_watermark_roundtrip_and_excluded_from_fnrs() -> None:
    reg = _registry()
    reg.ensure("030435h", source="x")
    assert reg.get_watermark().last_change_date is None
    reg.set_watermark("2026-06-16")
    assert reg.get_watermark().last_change_date == "2026-06-16"
    # the watermark singleton is not a company
    assert reg.all_fnrs() == ["030435h"]
    assert reg.count() == 1
