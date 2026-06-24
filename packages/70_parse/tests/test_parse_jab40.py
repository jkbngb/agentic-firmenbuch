"""Semantic JAb 4.0 parse tests (§15b-2) — validated against a REAL filing.

The real JAb 4.0 schema puts the value in a child ``POSTENZEILE/BETRAG_GJ`` under a
semantic parent element (``AKTIVA``, ``EIGENKAPITAL``, …). The earlier synthetic
flat-tag test gave false confidence and masked a critical empty-parse bug; these tests
assert the real fixture's exact numbers and the empty-jab40 dead-letter guardrail.
"""

from __future__ import annotations

from pathlib import Path

from builders import firmenbuch_2025_xml, jab40_xml

from fbl_parse import parse_filing


def test_jab40_real_fixture_exact_numbers(fixtures_dir: Path) -> None:
    pf = parse_filing((fixtures_dir / "030536g_2025-12-31_jab40.xml").read_bytes(), run_id="t")
    assert pf.format == "jab40_semantic"
    assert pf.parsed is True and pf.has_bilanz is True
    assert pf.fnr == "030536g"  # from FIRMENBUCHNUMMER
    assert pf.stichtag == "2025-12-31"
    assert pf.gkl == "W"  # from GROESSENKLASSE
    assert pf.currency == "EUR"
    b = pf.bilanz
    assert b.bilanzsumme == 294139.96  # AKTIVA/POSTENZEILE/BETRAG_GJ — the critical value
    assert b.eigenkapital == 207422.27
    assert b.verbindlichkeiten == 22831.04
    assert b.anlagevermoegen == 11137.86
    assert b.umlaufvermoegen == 282643.30
    assert b.sachanlagen == 11137.86
    assert b.vorraete == 5576.20
    assert b.forderungen == 179667.64
    assert b.cash == 97399.46
    assert b.rueckstellungen == 63886.65
    assert b.stammkapital == 36336.42
    assert b.bilanzgewinn_verlust == 171085.85
    assert pf.meta.checks["aktiva_equals_passiva"] is True
    # signatory from AUFSTELLENDE_PERSONEN/PERSON (fixture sanitized: year only retained)
    assert pf.signatory is not None
    assert pf.signatory.birth_year == 1970
    assert pf.signatory.age_at_signing is not None


def test_jab40_synthetic_nested_structure() -> None:
    pf = parse_filing(jab40_xml(), run_id="t")
    assert pf.format == "jab40_semantic"
    assert pf.fnr == "777777x" and pf.gkl == "W"
    assert pf.bilanz.bilanzsumme == 5000.0  # AKTIVA/POSTENZEILE/BETRAG_GJ
    assert pf.bilanz.eigenkapital == 2500.0
    assert pf.bilanz.verbindlichkeiten == 2000.0
    assert pf.bilanz.anlagevermoegen == 1000.0
    assert pf.bilanz.umlaufvermoegen == 4000.0
    assert pf.meta.checks["aktiva_equals_passiva"] is True


def test_jab40_thousands_scaling() -> None:
    pf = parse_filing(jab40_xml(einheit="T"), run_id="t")
    assert pf.bilanz.bilanzsumme == 5_000_000.0  # x1000 via EINHEIT=T
    assert pf.meta.checks["wert_tsd_applied"] is True


def test_jab40_empty_dead_letters() -> None:
    # A jab40 filing with the namespace but no extractable positions must dead-letter,
    # never be served as an empty company (§15b-2 guardrail).
    ns = "ns://justiz.gv.at/Bilanzierung/v4.0/Bilanz"
    empty = (
        f'<?xml version="1.0"?><UEBERMITTLUNG xmlns="{ns}"><ALLGEMEINE_ANGABEN>'
        "<FIRMENDATEN><FIRMENBUCHNUMMER>999999z</FIRMENBUCHNUMMER></FIRMENDATEN>"
        "<GESCHAEFTSJAHR><ENDE>2026-12-31</ENDE></GESCHAEFTSJAHR></ALLGEMEINE_ANGABEN>"
        "</UEBERMITTLUNG>"
    ).encode()
    pf = parse_filing(empty, run_id="t")
    assert pf.parsed is False
    assert pf.error is not None and "no positions" in pf.error
    assert pf.format == "jab40_semantic"


def test_firmenbuch_2025_betrag_gj() -> None:
    pf = parse_filing(firmenbuch_2025_xml(), run_id="t")
    assert pf.format == "firmenbuch_2025"
    assert pf.stichtag == "2025-12-31"
    assert pf.bilanz.bilanzsumme == 2000.0
    assert pf.bilanz.eigenkapital == 900.0
    assert pf.meta.checks["aktiva_equals_passiva"] is True
