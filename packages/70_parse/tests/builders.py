"""Synthetic-XML builders for the parse tests (importable helper, not a conftest)."""

from __future__ import annotations

NS = "https://finanzonline.bmf.gv.at/bilanz"


def legacy_xml(
    *,
    fnr: str = "999999z",
    gj_ende: str = "2024-12-31",
    aktiva: str = "1000.00",
    passiva: str = "1000.00",
    eigenkapital: str = "500.00",
    wert_tsd: str | None = None,
    extra_positions: str = "",
    unter: str = "",
    employees: str | None = None,
) -> bytes:
    """Build a minimal legacy_finanzonline filing for edge-case tests."""
    wert = f"<WERT_TSD>{wert_tsd}</WERT_TSD>" if wert_tsd else ""
    emp = (
        f"<HGB_Form_3><HGB_Form_3_16><ANZAHL>{employees}</ANZAHL></HGB_Form_3_16></HGB_Form_3>"
        if employees is not None
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<UEBERMITTLUNG xmlns="{NS}">
  <BILANZ_GLIEDERUNG ART="HGB">
    <ALLG_JUSTIZ>
      <FIRMA><FNR>{fnr}</FNR><F_NAME><Z>Test GmbH</Z></F_NAME></FIRMA>
      <GJ><BEGINN>2024-01-01</BEGINN><ENDE>{gj_ende}</ENDE>{wert}</GJ>
      <VOR_GJ><ENDE>2023-12-31</ENDE><WERT_TSD>n</WERT_TSD></VOR_GJ>
      <WAEHRUNG>EUR</WAEHRUNG>
      {unter}
    </ALLG_JUSTIZ>
    <HGB_Form_2>
      <HGB_224_2><POSTENZEILE><BETRAG>{aktiva}</BETRAG></POSTENZEILE></HGB_224_2>
      <HGB_224_3><POSTENZEILE><BETRAG>{passiva}</BETRAG></POSTENZEILE>
        <HGB_224_3_A><POSTENZEILE><BETRAG>{eigenkapital}</BETRAG></POSTENZEILE></HGB_224_3_A>
      </HGB_224_3>
      {extra_positions}
    </HGB_Form_2>
    {emp}
  </BILANZ_GLIEDERUNG>
</UEBERMITTLUNG>""".encode()


def firmenbuch_2025_xml(*, fnr: str = "888888y", gj_ende: str = "2025-12-31") -> bytes:
    """Build a minimal firmenbuch_2025 filing (BETRAG_GJ + GESCHAEFTSJAHR)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<UEBERMITTLUNG xmlns="{NS}">
  <BILANZ_GLIEDERUNG ART="HGB">
    <ALLG_JUSTIZ>
      <FIRMA><FNR>{fnr}</FNR></FIRMA>
      <GESCHAEFTSJAHR><BEGINN>2025-01-01</BEGINN><ENDE>{gj_ende}</ENDE></GESCHAEFTSJAHR>
      <WAEHRUNG>EUR</WAEHRUNG>
    </ALLG_JUSTIZ>
    <HGB_Form_2>
      <HGB_224_2><BETRAG_GJ>2000.00</BETRAG_GJ></HGB_224_2>
      <HGB_224_3><BETRAG_GJ>2000.00</BETRAG_GJ>
        <HGB_224_3_A><BETRAG_GJ>900.00</BETRAG_GJ></HGB_224_3_A>
      </HGB_224_3>
    </HGB_Form_2>
  </BILANZ_GLIEDERUNG>
</UEBERMITTLUNG>""".encode()


def _pz(betrag_gj: str) -> str:
    return (
        f"<POSTENZEILE><BETRAG_GJ>{betrag_gj}</BETRAG_GJ><BETRAG_VJ>0.00</BETRAG_VJ></POSTENZEILE>"
    )


def jab40_xml(*, fnr: str = "777777x", gj_ende: str = "2026-12-31", einheit: str = "E") -> bytes:
    """Build a jab40_semantic filing in the REAL schema shape.

    Value lives in a child ``POSTENZEILE/BETRAG_GJ`` under a semantic parent element —
    NOT as the element's own text (that was the false-confidence bug). ``einheit='T'``
    marks thousands.
    """
    ns = "ns://justiz.gv.at/Bilanzierung/v4.0/Bilanz"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<UEBERMITTLUNG xmlns="{ns}">
  <ALLGEMEINE_ANGABEN>
    <FIRMENDATEN><FIRMENBUCHNUMMER>{fnr}</FIRMENBUCHNUMMER>
      <FIRMENWORTLAUT><ZEILE>Test GmbH</ZEILE></FIRMENWORTLAUT></FIRMENDATEN>
    <GESCHAEFTSJAHR><BEGINN>2026-01-01</BEGINN><ENDE>{gj_ende}</ENDE><RECHTSFORM>GES</RECHTSFORM>
      <GROESSENKLASSE>W</GROESSENKLASSE>
      <EINGABEDARSTELLUNG><DARSTELLUNG_EINGEREICHT><EINHEIT>{einheit}</EINHEIT>
        <NACHKOMMASTELLEN>N</NACHKOMMASTELLEN></DARSTELLUNG_EINGEREICHT></EINGABEDARSTELLUNG>
    </GESCHAEFTSJAHR>
    <BILANZ_WAEHRUNG>EUR</BILANZ_WAEHRUNG>
    <AUFSTELLENDE_PERSONEN><PERSON><GEBURTSDATUM>1970-05-15</GEBURTSDATUM>
      <VORNAME>Max</VORNAME><NACHNAME>Mustermann</NACHNAME>
      <DATUM_UNTERSCHRIFT>2027-04-07</DATUM_UNTERSCHRIFT></PERSON></AUFSTELLENDE_PERSONEN>
  </ALLGEMEINE_ANGABEN>
  <ANLAGE1_AUSZUG_AUS_DER_BILANZ>
    <AKTIVA>{_pz("5000.00")}
      <ANLAGEVERMOEGEN>{_pz("1000.00")}<SACHANLAGEN>{_pz("1000.00")}</SACHANLAGEN></ANLAGEVERMOEGEN>
      <UMLAUFVERMOEGEN>{_pz("4000.00")}</UMLAUFVERMOEGEN>
    </AKTIVA>
    <PASSIVA>{_pz("5000.00")}
      <EIGENKAPITAL>{_pz("2500.00")}</EIGENKAPITAL>
      <VERBINDLICHKEITEN>{_pz("2000.00")}</VERBINDLICHKEITEN>
    </PASSIVA>
  </ANLAGE1_AUSZUG_AUS_DER_BILANZ>
</UEBERMITTLUNG>""".encode()
