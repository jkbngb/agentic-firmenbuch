"""Consolidation tests on the multi-year fixture (§8.5 DoD)."""

from __future__ import annotations

from pathlib import Path

from fbl_consolidate import consolidate
from fbl_core.lineage import content_hash
from fbl_core.models import Bilanz, FieldProvenance, GuV, MasterData, Meta, ParsedFiling, Signatory
from fbl_parse import parse_filing


def _multiyear(fixtures_dir: Path) -> list[ParsedFiling]:
    files = sorted((fixtures_dir / "raw" / "490875a_multiyear").glob("*.xml"))
    return [parse_filing(f.read_bytes(), run_id="t") for f in files]


def test_multiyear_histories(fixtures_dir: Path) -> None:
    cons = consolidate("490875a", _multiyear(fixtures_dir), master=None, prev=None, run_id="t")
    assert cons.company.first_filing_year == 2018
    assert cons.company.last_filing_year == 2024
    assert cons.company.filing_years_available == 7
    bs = cons.financials.bilanz["bilanzsumme"]
    assert bs.history[2018] == 1459206.99
    assert bs.history[2024] == 1648140.11
    assert bs.latest == 1648140.11 and bs.latest_year == 2024
    # Bilanz-only company -> no GuV (§15b-9)
    assert cons.financials.has_guv is False
    assert cons.financials.has_guv_latest is False
    assert cons.financials.guv == {}


def test_gkl_and_management(fixtures_dir: Path) -> None:
    cons = consolidate("490875a", _multiyear(fixtures_dir), master=None, prev=None, run_id="t")
    assert cons.filings[0].gkl == "K"  # from EINSTUFUNG
    assert cons.management is not None
    assert cons.management.n_signatories_latest == 2
    assert cons.management.signatories_stable_years == 7  # same 2 signatories every year


def test_dedupe_resubmission_keeps_latest() -> None:
    def filing(value: float) -> ParsedFiling:
        return ParsedFiling(
            fnr="030435h",
            stichtag="2024-12-31",
            format="legacy_finanzonline",
            parsed=True,
            has_bilanz=True,
            bilanz=Bilanz(bilanzsumme=value),
            field_provenance=FieldProvenance(format="legacy_finanzonline"),
            meta=Meta(
                doc_id="d",
                entity_id="030435h/2024-12-31",
                stage="parsed",
                producer="parse@1.0.0",
                run_id="t",
            ),
        )

    cons = consolidate("030435h", [filing(100.0), filing(200.0)], None, None, run_id="t")
    # one Stichtag -> last submission wins
    assert cons.financials.bilanz["bilanzsumme"].history == {2024: 200.0}


def _pf(fnr: str, stichtag: str, aktiva: float, prior_aktiva: float | None = None) -> ParsedFiling:
    return ParsedFiling(
        fnr=fnr,
        stichtag=stichtag,
        format="legacy_finanzonline",
        parsed=True,
        has_bilanz=True,
        bilanz=Bilanz(bilanzsumme=aktiva),
        positions={"aktiva": aktiva},
        positions_prior_year=({"aktiva": prior_aktiva} if prior_aktiva is not None else {}),
        field_provenance=FieldProvenance(format="legacy_finanzonline"),
        meta=Meta(
            doc_id="d",
            entity_id=f"{fnr}/{stichtag}",
            stage="parsed",
            producer="parse@1.0.0",
            run_id="t",
        ),
    )


def test_prior_year_reconciliation_passes_when_columns_agree() -> None:
    # 2024 filing's prior-year aktiva (1000) matches 2023 filing's current aktiva (1000).
    filings = [_pf("x", "2023-12-31", 1000.0), _pf("x", "2024-12-31", 1200.0, prior_aktiva=1000.0)]
    cons = consolidate("x", filings, None, None, run_id="t")
    assert cons.meta.checks["prior_year_reconciled"] is True


def test_prior_year_reconciliation_fails_on_mismatch() -> None:
    # 2024 filing claims prior-year aktiva 900, but 2023 filed 1000 (>1% apart).
    filings = [_pf("x", "2023-12-31", 1000.0), _pf("x", "2024-12-31", 1200.0, prior_aktiva=900.0)]
    cons = consolidate("x", filings, None, None, run_id="t")
    assert cons.meta.checks["prior_year_reconciled"] is False


def _guv_filing(stichtag: str, *, umsatz: float | None) -> ParsedFiling:
    guv = GuV(revenue_basis="umsatzerloese", umsatzerloese=umsatz) if umsatz is not None else None
    return ParsedFiling(
        fnr="x",
        stichtag=stichtag,
        format="legacy_finanzonline",
        parsed=True,
        has_bilanz=True,
        has_guv=guv is not None,
        bilanz=Bilanz(bilanzsumme=1000.0),
        guv=guv,
        field_provenance=FieldProvenance(format="legacy_finanzonline"),
        meta=Meta(doc_id="d", entity_id=f"x/{stichtag}", stage="parsed", producer="p", run_id="t"),
    )


def test_history_tolerates_year_gaps() -> None:
    # §15b-11: non-consecutive fiscal years (a gap) must consolidate without error.
    filings = [_pf("x", "2019-12-31", 100.0), _pf("x", "2022-12-31", 300.0)]
    cons = consolidate("x", filings, None, None, run_id="t")
    bs = cons.financials.bilanz["bilanzsumme"]
    assert set(bs.history) == {2019, 2022}  # gap preserved, not interpolated
    assert bs.latest_year == 2022
    assert cons.company.filing_years_available == 2


def test_has_guv_latest_reflects_only_the_latest_year() -> None:
    # §15b-12: GuV in an earlier year only -> has_guv (ever) True, has_guv_latest False.
    filings = [_guv_filing("2022-12-31", umsatz=5000.0), _guv_filing("2023-12-31", umsatz=None)]
    cons = consolidate("x", filings, None, None, run_id="t")
    assert cons.financials.has_guv is True  # GuV existed in some year
    assert cons.financials.has_guv_latest is False  # but not the latest filing
    assert cons.financials.guv_years == [2022]


def test_series_carries_source_codes_and_paragraph_ref(fixtures_dir: Path) -> None:
    # Part A.2: each position series carries the official code(s) + the §-reference.
    cons = consolidate("490875a", _multiyear(fixtures_dir), master=None, prev=None, run_id="t")
    bs = cons.financials.bilanz["bilanzsumme"]
    assert bs.source_codes == ["HGB_224_2"]
    assert bs.paragraph_ref == "§224 Abs 2"
    assert bs.source_codes_by_year == {}  # same code every year -> not recorded per-year


def test_source_codes_recorded_per_year_when_they_differ() -> None:
    # Part A.2: when the code differs across years for a canonical, record it per year.
    def pf(stichtag: str, code: str) -> ParsedFiling:
        return ParsedFiling(
            fnr="x",
            stichtag=stichtag,
            format="legacy_finanzonline",
            parsed=True,
            has_bilanz=True,
            bilanz=Bilanz(bilanzsumme=100.0),
            positions={"aktiva": 100.0},
            position_codes={"aktiva": [code]},
            field_provenance=FieldProvenance(format="legacy_finanzonline"),
            meta=Meta(
                doc_id="d", entity_id=f"x/{stichtag}", stage="parsed", producer="p", run_id="t"
            ),
        )

    # 2023 filed under the legacy HGB code, 2024 migrated to the JAb 4.0 element name.
    filings = [pf("2023-12-31", "HGB_224_2"), pf("2024-12-31", "AKTIVA")]
    cons = consolidate("x", filings, None, None, run_id="t")
    bs = cons.financials.bilanz["bilanzsumme"]
    assert bs.source_codes == ["AKTIVA", "HGB_224_2"]  # union (sorted)
    assert bs.source_codes_by_year == {2023: ["HGB_224_2"], 2024: ["AKTIVA"]}
    assert bs.paragraph_ref == "§224 Abs 2"  # stable §-ref via the canonical


def test_rebuild_unchanged_is_noop(fixtures_dir: Path) -> None:
    filings = _multiyear(fixtures_dir)
    first = consolidate("490875a", filings, None, prev=None, run_id="t")
    assert first.meta.data_version == 1
    assert first.meta.supersedes is None
    # Identical inputs -> no version bump (true no-op) and identical content hash.
    second = consolidate("490875a", filings, None, prev=first, run_id="t2")
    assert second.meta.data_version == 1
    assert second.meta.supersedes is None
    assert content_hash(first.model_dump(mode="json")) == content_hash(
        second.model_dump(mode="json")
    )


def test_rebuild_with_change_bumps_and_supersedes(fixtures_dir: Path) -> None:
    filings = _multiyear(fixtures_dir)
    first = consolidate("490875a", filings, None, prev=None, run_id="t")
    # A new filing arrives -> content changes -> data_version bumps + supersedes set.
    extra = ParsedFiling(
        fnr="490875a",
        stichtag="2025-12-31",
        format="legacy_finanzonline",
        parsed=True,
        has_bilanz=True,
        bilanz=Bilanz(bilanzsumme=1_700_000.0),
        field_provenance=FieldProvenance(format="legacy_finanzonline"),
        meta=Meta(
            doc_id="d",
            entity_id="490875a/2025-12-31",
            stage="parsed",
            producer="parse@1.0.0",
            run_id="t",
        ),
    )
    second = consolidate("490875a", [*filings, extra], None, prev=first, run_id="t2")
    assert second.meta.data_version == 2
    assert second.meta.supersedes is not None
    assert second.meta.supersedes.doc_id == first.meta.doc_id
    assert content_hash(first.model_dump(mode="json")) != content_hash(
        second.model_dump(mode="json")
    )


def test_identity_name_falls_back_to_filing_name(fixtures_dir: Path) -> None:
    # No master -> the name carried in the filing (§15b-5) fills identity, not the FNR.
    filings = _filings_030435h(fixtures_dir)
    cons = consolidate("030435h", filings, master=None, prev=None, run_id="t")
    assert cons.identity.name == "WWN Westerthaler Warenhandels- und Nagelstudio GmbH"


def _filings_030435h(fixtures_dir: Path) -> list[ParsedFiling]:
    f = fixtures_dir / "raw" / "030435h_2020-03-31_jb.xml"
    return [parse_filing(f.read_bytes(), run_id="t")]


def test_master_data_populates_identity_and_location(fixtures_dir: Path) -> None:
    master = MasterData(
        fnr="490875a",
        name="Walter Wagner Transporte GmbH",
        legal_form="GES",
    )
    from fbl_core.models import Location

    master.location = Location(bundesland="T", city="Innsbruck", postal_code="6020")
    cons = consolidate("490875a", _multiyear(fixtures_dir), master=master, prev=None, run_id="t")
    assert cons.identity.name == "Walter Wagner Transporte GmbH"
    assert cons.identity.legal_form == "GES"
    assert cons.location.city == "Innsbruck"
    assert any(ref.source == "auszug" for ref in cons.meta.inputs)


def test_signatory_year_only_carried() -> None:
    pf = ParsedFiling(
        fnr="030435h",
        stichtag="2020-03-31",
        format="legacy_finanzonline",
        parsed=True,
        has_bilanz=True,
        bilanz=Bilanz(bilanzsumme=1.0),
        signatory=Signatory(first_name="A", last_name="B", birth_year=1962, age_at_signing=58.1),
        signatories=[Signatory(first_name="A", last_name="B", birth_year=1962)],
        field_provenance=FieldProvenance(format="legacy_finanzonline"),
        meta=Meta(
            doc_id="d",
            entity_id="030435h/2020-03-31",
            stage="parsed",
            producer="parse@1.0.0",
            run_id="t",
        ),
    )
    cons = consolidate("030435h", [pf], None, None, run_id="t")
    assert cons.management is not None
    assert cons.management.primary_gf is not None
    assert cons.management.primary_gf.birth_year == 1962
