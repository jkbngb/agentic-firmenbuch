"""Edge cases & known data gotchas (§15b)."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders import legacy_xml

from fbl_parse import parse_filing, parse_pdf_only
from fbl_parse.xml_common import glue_name


def test_wert_tsd_scales_by_1000() -> None:
    # §15b-4: WERT_TSD=j means the current-year column is in thousands.
    pf = parse_filing(legacy_xml(aktiva="171.96", passiva="171.96", wert_tsd="j"), run_id="t")
    assert pf.bilanz.bilanzsumme == 171960.0
    assert pf.meta.checks["wert_tsd_applied"] is True
    assert pf.field_provenance.scaling == {"wert_tsd_applied": True}


def test_no_wert_tsd_no_scaling() -> None:
    pf = parse_filing(legacy_xml(aktiva="171.96", passiva="171.96"), run_id="t")
    assert pf.bilanz.bilanzsumme == 171.96
    assert pf.meta.checks["wert_tsd_applied"] is False


def test_unknown_code_goes_to_passthrough() -> None:
    # §15b-3: a code absent from the taxonomy must be preserved, not dropped.
    extra = (
        "<XXX_999_TOTALLY_MADE_UP><POSTENZEILE><BETRAG>42.50</BETRAG>"
        "</POSTENZEILE></XXX_999_TOTALLY_MADE_UP>"
    )
    pf = parse_filing(legacy_xml(extra_positions=extra), run_id="t")
    assert pf.field_provenance.passthrough["XXX_999_TOTALLY_MADE_UP"] == 42.50


def test_negative_equity_flagged() -> None:
    # §15b-10: negative Eigenkapital is legal and real.
    pf = parse_filing(legacy_xml(eigenkapital="-1500.00"), run_id="t")
    assert pf.bilanz.eigenkapital == -1500.0
    assert pf.meta.checks["negative_equity"] is True


def test_malformed_birth_date_does_not_crash() -> None:
    # §15b-14: malformed GEB_DAT -> null, no crash.
    unter = (
        "<UNTER><GEB_DAT>not-a-date</GEB_DAT><V_NAME>Max</V_NAME>"
        "<Z_NAME>Muster</Z_NAME><DAT_UNT>2025-01-01</DAT_UNT></UNTER>"
    )
    pf = parse_filing(legacy_xml(unter=unter), run_id="t")
    assert pf.signatory is not None
    assert pf.signatory.birth_year is None
    assert pf.signatory.age_at_signing is None
    assert pf.signatory.first_name == "Max"


def test_partial_birth_date_year_only() -> None:
    unter = (
        "<UNTER><GEB_DAT>1980-06-15</GEB_DAT><V_NAME>Eva</V_NAME>"
        "<Z_NAME>Beispiel</Z_NAME><DAT_UNT>2025-06-15</DAT_UNT></UNTER>"
    )
    pf = parse_filing(legacy_xml(unter=unter), run_id="t")
    sig = pf.signatory
    assert sig is not None
    assert sig.birth_year == 1980
    assert sig.age_at_signing == 45.0
    # The full date/month/day is never retained on the model.
    assert "06" not in (sig.signed_at or "").replace("2025-06-15", "")


def test_pers_kenn_sibling_fallback() -> None:
    # §15b-15: PERS_KENN can live in a sibling list, not inside UNTER.
    unter = (
        "<UNTER><V_NAME>A</V_NAME><Z_NAME>One</Z_NAME></UNTER>"
        "<UNTER><V_NAME>B</V_NAME><Z_NAME>Two</Z_NAME></UNTER>"
        "<PERS_KENN>A</PERS_KENN><PERS_KENN>B</PERS_KENN>"
    )
    pf = parse_filing(legacy_xml(unter=unter), run_id="t")
    assert [s.role_code for s in pf.signatories] == ["A", "B"]


def test_second_birth_date_source() -> None:
    # §15b-16: alternate PERSON/GEBURTSDATUM source.
    unter = (
        "<UNTER><PERSON><GEBURTSDATUM>1975-03-03</GEBURTSDATUM></PERSON>"
        "<V_NAME>Kim</V_NAME><Z_NAME>X</Z_NAME><DAT_UNT>2025-03-03</DAT_UNT></UNTER>"
    )
    pf = parse_filing(legacy_xml(unter=unter), run_id="t")
    assert pf.signatory is not None and pf.signatory.birth_year == 1975
    assert pf.signatory.age_at_signing == 50.0


def test_parse_error_yields_dead_letter_stub() -> None:
    # §15b-7: unparseable input -> stub with error, never crash.
    pf = parse_filing(b"<UEBERMITTLUNG><broken", run_id="t", fnr_hint="123456a")
    assert pf.parsed is False
    assert pf.error is not None and "xml_syntax" in pf.error
    assert pf.fnr == "123456a"
    assert pf.meta.content_hash is not None  # stub is still a valid, hashed doc


def test_bilanz_only_has_no_guv() -> None:
    # §15b-9: most companies file Bilanz only.
    pf = parse_filing(legacy_xml(), run_id="t")
    assert pf.has_bilanz is True
    assert pf.has_guv is False
    assert pf.guv is None


def test_pdf_only_stub() -> None:
    pf = parse_pdf_only("093450b", "2025-12-31", run_id="t")
    assert pf.format == "pdf"
    assert pf.parsed is False
    assert pf.has_bilanz is False and pf.guv is None
    assert pf.meta.entity_id == "093450b/2025-12-31"


def test_glue_name_hyphen_rules() -> None:
    # §15b-5: hyphen-gluing of multi-line names.
    assert glue_name(["Waren-", "handel GmbH"]) == "Warenhandel GmbH"
    assert (
        glue_name(["WWN Westerthaler", "Nagelstudio GmbH"]) == "WWN Westerthaler Nagelstudio GmbH"
    )
    assert glue_name(["  Solo GmbH  "]) == "Solo GmbH"
    assert glue_name([]) is None


def test_company_name_extracted_from_filing(fixtures_dir: Path) -> None:
    # §15b-5: the company name is read from the filing and glued from its segments.
    legacy = parse_filing((fixtures_dir / "030435h_2020-03-31_jb.xml").read_bytes(), run_id="t")
    assert legacy.name == "WWN Westerthaler Warenhandels- und Nagelstudio GmbH"
    jab40 = parse_filing((fixtures_dir / "030536g_2025-12-31_jab40.xml").read_bytes(), run_id="t")
    assert jab40.name == "Dayal GmbH"  # FIRMENWORTLAUT/ZEILE


def test_position_mapped_by_code_not_label(fixtures_dir: Path) -> None:
    # §15b-6: Justiz's own label typos ("Kaßenbestand", "Erzeugniße") are authoritative;
    # mapping is by element CODE, so a garbled label child must not change the result.
    cash = (
        "<HGB_224_2_B_IV>"
        "<BEZEICHNUNG>Kaßenbestand, Erzeugniße und außtehende Einlagen</BEZEICHNUNG>"
        "<POSTENZEILE><BETRAG>123.45</BETRAG></POSTENZEILE>"
        "</HGB_224_2_B_IV>"
    )
    pf = parse_filing(legacy_xml(extra_positions=cash), run_id="t")
    assert pf.positions["kassenbestand_schecks_guthaben_bei_kreditinstituten"] == 123.45


def test_betrag_vj_not_used_as_value_of_record() -> None:
    # §15b-8: the prior-year column (BETRAG_VJ) is captured for reconciliation only;
    # the current-year column (BETRAG_GJ) is the authoritative value.
    ns = "ns://justiz.gv.at/Bilanzierung/v4.0/Bilanz"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<UEBERMITTLUNG xmlns="{ns}">
  <ALLGEMEINE_ANGABEN>
    <FIRMENDATEN><FIRMENBUCHNUMMER>777777x</FIRMENBUCHNUMMER></FIRMENDATEN>
    <GESCHAEFTSJAHR><BEGINN>2026-01-01</BEGINN><ENDE>2026-12-31</ENDE></GESCHAEFTSJAHR>
    <BILANZ_WAEHRUNG>EUR</BILANZ_WAEHRUNG>
  </ALLGEMEINE_ANGABEN>
  <ANLAGE1_AUSZUG_AUS_DER_BILANZ>
    <AKTIVA><POSTENZEILE><BETRAG_GJ>5000.00</BETRAG_GJ><BETRAG_VJ>4000.00</BETRAG_VJ></POSTENZEILE>
    </AKTIVA>
  </ANLAGE1_AUSZUG_AUS_DER_BILANZ>
</UEBERMITTLUNG>""".encode()
    pf = parse_filing(xml, run_id="t")
    assert pf.positions["aktiva"] == 5000.00  # current year is authoritative
    assert pf.positions_prior_year["aktiva"] == 4000.00  # prior captured, never the value


def test_source_codes_recorded_per_position(fixtures_dir: Path) -> None:
    # Part A.1: the exact official code each canonical was parsed from is kept.
    legacy = parse_filing((fixtures_dir / "030435h_2020-03-31_jb.xml").read_bytes(), run_id="t")
    assert legacy.position_codes["aktiva"] == ["HGB_224_2"]
    assert legacy.position_codes["eigenkapital"] == ["HGB_224_3_A"]
    jab40 = parse_filing((fixtures_dir / "030536g_2025-12-31_jab40.xml").read_bytes(), run_id="t")
    assert jab40.position_codes["aktiva"] == ["AKTIVA"]  # the v4 element name is the code
    assert jab40.position_codes["eigenkapital"] == ["EIGENKAPITAL"]


def test_source_code_collision_keeps_both_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    # Part A.1: two distinct codes mapping to one canonical -> BOTH kept, value not
    # overwritten, collision logged. aktive_latente_steuern = HGB_224_2_D OR HGB_Form_3_26.
    both = (
        "<HGB_224_2_D><POSTENZEILE><BETRAG>10.00</BETRAG></POSTENZEILE></HGB_224_2_D>"
        "<HGB_Form_3_26><POSTENZEILE><BETRAG>99.00</BETRAG></POSTENZEILE></HGB_Form_3_26>"
    )
    with caplog.at_level("WARNING"):
        pf = parse_filing(legacy_xml(extra_positions=both), run_id="t")
    assert pf.position_codes["aktive_latente_steuern"] == ["HGB_224_2_D", "HGB_Form_3_26"]
    assert pf.positions["aktive_latente_steuern"] == 10.00  # first wins for the value
    assert any("collision" in r.message for r in caplog.records)


def test_jab40_passthrough_excludes_structural_carriers(fixtures_dir: Path) -> None:
    # Part B hygiene: POSTENZEILE/BETRAG_GJ/BETRAG_VJ are value carriers, not positions,
    # so they must NOT pollute passthrough (which is reserved for genuine unknown codes).
    jab40 = parse_filing((fixtures_dir / "030536g_2025-12-31_jab40.xml").read_bytes(), run_id="t")
    pt = jab40.field_provenance.passthrough
    assert "POSTENZEILE" not in pt and "BETRAG_GJ" not in pt and "BETRAG_VJ" not in pt


def test_free_slot_positions_captured_in_passthrough() -> None:
    # §5.1 no-loss: a non-HGB free-text slot carrying a real BETRAG must NOT be dropped.
    # Two FREI rows with distinct labels must BOTH survive (collision-safe keying).
    free = (
        "<FREI><POSTENZEILE><BETRAG>56209.80</BETRAG>"
        "<TEXT>COVID-19 Kurzarbeitsbeihilfe</TEXT></POSTENZEILE></FREI>"
        "<FREI><POSTENZEILE><BETRAG>-553126.51</BETRAG>"
        "<TEXT>Veränderung aktive latente Steuern</TEXT></POSTENZEILE></FREI>"
        "<FREIER_SUB_POSTEN><BETRAG>71204549.82</BETRAG>"
        "<TEXT>Gewinnvortrag aus Vorjahren</TEXT></FREIER_SUB_POSTEN>"
    )
    pf = parse_filing(legacy_xml(extra_positions=free), run_id="t")
    pt = pf.field_provenance.passthrough
    assert pt["FREI: COVID-19 Kurzarbeitsbeihilfe"] == 56209.80
    assert pt["FREI: Veränderung aktive latente Steuern"] == -553126.51  # 2nd FREI kept too
    assert pt["FREIER_SUB_POSTEN: Gewinnvortrag aus Vorjahren"] == 71204549.82


def test_employees_extracted() -> None:
    pf = parse_filing(legacy_xml(employees="17"), run_id="t")
    assert pf.employees == 17
