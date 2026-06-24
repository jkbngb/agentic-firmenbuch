"""Exact-number parse tests on the real legacy fixtures (§8.4 DoD)."""

from __future__ import annotations

from pathlib import Path

from fbl_core.lineage import content_hash
from fbl_core.models.filing import ParsedFiling
from fbl_parse import parse_filing


def _parse(path: Path) -> ParsedFiling:
    return parse_filing(path.read_bytes(), run_id="test")


def test_030435h_exact_numbers(fixtures_dir: Path) -> None:
    pf = _parse(fixtures_dir / "030435h_2020-03-31_jb.xml")
    assert pf.format == "legacy_finanzonline"
    assert pf.fnr == "030435h"
    assert pf.stichtag == "2020-03-31"
    assert pf.gj_beginn == "2019-04-01" and pf.gj_ende == "2020-03-31"
    assert pf.currency == "EUR"
    assert pf.parsed is True and pf.has_bilanz is True and pf.has_guv is False
    b = pf.bilanz
    assert b.bilanzsumme == 171959.99
    assert b.eigenkapital == 83728.15
    assert b.verbindlichkeiten == 81815.74
    assert b.anlagevermoegen == 8721.15
    assert b.umlaufvermoegen == 163238.84
    assert b.cash == 2893.99
    assert b.vorraete == 51951.34
    assert b.forderungen == 108393.51
    assert b.rueckstellungen == 6416.10
    assert b.stammkapital == 36336.42
    assert b.bilanzgewinn_verlust == -60472.38  # negative retained earnings, legal
    assert pf.employees == 3
    assert pf.meta.checks["aktiva_equals_passiva"] is True
    assert pf.meta.checks["negative_equity"] is False  # equity itself is positive


def test_030435h_signatory_age(fixtures_dir: Path) -> None:
    pf = _parse(fixtures_dir / "030435h_2020-03-31_jb.xml")
    sig = pf.signatory
    assert sig is not None
    assert sig.first_name == "Astrid" and sig.last_name == "Westerthaler"
    assert sig.birth_year == 1962  # year only, never the full date
    assert sig.signed_at == "2020-12-09"
    # (2020-12-09 minus 1962-11-03)/365.25 = 58.1
    assert sig.age_at_signing == 58.1


def test_030435h_recognized_non_model_position_preserved(fixtures_dir: Path) -> None:
    # XXX_224_3_D_X is in the taxonomy (hybride_finanzinstrumente); it is not one of
    # the 15 typed Bilanz fields but must still be preserved in the positions map.
    pf = _parse(fixtures_dir / "030435h_2020-03-31_jb.xml")
    assert pf.positions["hybride_finanzinstrumente"] == 96108.97
    assert pf.field_provenance.passthrough == {}  # known code -> not passthrough


def test_490875a_2024_numbers_and_roles(fixtures_dir: Path) -> None:
    pf = _parse(fixtures_dir / "490875a_multiyear" / "490875a_2024-12-31.xml")
    assert pf.fnr == "490875a" and pf.stichtag == "2024-12-31"
    assert pf.bilanz.bilanzsumme == 1648140.11
    assert pf.bilanz.eigenkapital == 882046.01
    assert pf.employees == 34
    assert len(pf.signatories) == 2
    assert [s.role_code for s in pf.signatories] == ["A", "B"]
    # No GEB_DAT in these records -> birth_year/age are None (partial coverage §15b-13)
    assert pf.signatory is not None and pf.signatory.role_code == "A"
    assert pf.signatory.birth_year is None and pf.signatory.age_at_signing is None


def test_aktiva_equals_passiva_all_multiyear(fixtures_dir: Path) -> None:
    for path in sorted((fixtures_dir / "490875a_multiyear").glob("*.xml")):
        pf = _parse(path)
        assert pf.meta.checks["aktiva_equals_passiva"] is True, path.name


def test_parse_is_idempotent(fixtures_dir: Path) -> None:
    path = fixtures_dir / "030636d_2023-05-31_jb.xml"
    raw = path.read_bytes()
    a = parse_filing(raw, run_id="run-1")
    b = parse_filing(raw, run_id="run-2")
    # Different run_id/doc_id, but identical business content -> identical hash.
    assert a.meta.doc_id != b.meta.doc_id
    assert content_hash(a.model_dump(mode="json")) == content_hash(b.model_dump(mode="json"))
    assert a.meta.content_hash == b.meta.content_hash


def test_lineage_chain_from_raw(fixtures_dir: Path) -> None:
    from fbl_core.models.meta import LineageRef

    raw_ref = LineageRef(
        stage="raw",
        doc_id="raw-doc-id",
        content_hash="sha256:rawhash",
        created_at="2026-06-16T05:00:12Z",
        producer="ingest@1.0.0",
    )
    pf = parse_filing(
        (fixtures_dir / "030435h_2020-03-31_jb.xml").read_bytes(),
        run_id="test",
        raw_ref=raw_ref,
    )
    assert pf.meta.lineage[0].doc_id == "raw-doc-id"
    assert pf.meta.timestamps["ingested_at"] == "2026-06-16T05:00:12Z"
    assert "parsed_at" in pf.meta.timestamps
