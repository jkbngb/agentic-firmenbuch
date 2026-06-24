"""A configurable fake RegisterSource for orchestration end-to-end tests."""

from __future__ import annotations

from datetime import date

from fbl_firmenbuch_client import (
    AuszugKurz,
    DocChange,
    FirmaChange,
    FirmaResult,
    UrkundeContent,
    UrkundeRef,
)


def legacy_xml(fnr: str, stichtag: str, bilanzsumme: float, eigenkapital: float) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<UEBERMITTLUNG xmlns="https://finanzonline.bmf.gv.at/bilanz">'
        '<BILANZ_GLIEDERUNG ART="HGB"><ALLG_JUSTIZ EINSTUFUNG="K">'
        f"<FIRMA><FNR>{fnr}</FNR></FIRMA>"
        f"<GJ><BEGINN>{stichtag[:4]}-01-01</BEGINN><ENDE>{stichtag}</ENDE></GJ>"
        "<WAEHRUNG>EUR</WAEHRUNG>"
        "<UNTER><PERS_KENN>A</PERS_KENN><V_NAME>Test</V_NAME><Z_NAME>Person</Z_NAME>"
        f"<GEB_DAT>1970-01-01</GEB_DAT><DAT_UNT>{stichtag[:4]}-06-01</DAT_UNT></UNTER>"
        "</ALLG_JUSTIZ><HGB_Form_2>"
        f"<HGB_224_2><POSTENZEILE><BETRAG>{bilanzsumme}</BETRAG></POSTENZEILE></HGB_224_2>"
        f"<HGB_224_3><POSTENZEILE><BETRAG>{bilanzsumme}</BETRAG></POSTENZEILE>"
        f"<HGB_224_3_A><POSTENZEILE><BETRAG>{eigenkapital}</BETRAG></POSTENZEILE></HGB_224_3_A>"
        "</HGB_224_3></HGB_Form_2></BILANZ_GLIEDERUNG></UEBERMITTLUNG>"
    ).encode()


class FakeSource:
    def __init__(
        self,
        *,
        universe: dict[str, str] | None = None,
        documents: dict[str, list[UrkundeRef]] | None = None,
        values: dict[str, tuple[float, float]] | None = None,
        firma_changes: list[FirmaChange] | None = None,
        doc_changes: list[DocChange] | None = None,
    ) -> None:
        self.universe = universe or {}
        self.documents = documents or {}
        self.values = values or {}  # stichtag -> (bilanzsumme, eigenkapital)
        self.firma_changes = firma_changes or []
        self.doc_changes = doc_changes or []
        self.queried_von: list[date] = []  # records the `von` of each change-feed query

    def suche_firma(self, firmenwortlaut: str, **kw: object) -> list[FirmaResult]:
        prefix = firmenwortlaut.rstrip("*").lower()
        return [
            FirmaResult(fnr=fnr, name=name, rechtsform_code="GES")
            for fnr, name in sorted(self.universe.items())
            if name.lower().startswith(prefix)
        ]

    def suche_urkunde(self, fnr: str) -> list[UrkundeRef]:
        return self.documents.get(fnr, [])

    def urkunde(self, key: str) -> UrkundeContent:
        fnr, stichtag, _ext = key.split("|")
        bs, ek = self.values.get(stichtag, (1000.0, 500.0))
        return UrkundeContent(
            key=key,
            fnr=fnr,
            content_type="application/xml",
            dateiendung="xml",
            content=legacy_xml(fnr, stichtag, bs, ek),
            format="legacy_finanzonline",
            stichtag=stichtag,
        )

    def auszug(self, fnr: str, *, stichtag: date | None = None) -> AuszugKurz:
        return AuszugKurz(
            fnr=fnr, name=self.universe.get(fnr, "Unknown GmbH"), city="Wien", postal_code="1010"
        )

    def veraenderungen_urkunden(self, von: date, bis: date) -> list[DocChange]:
        return self.doc_changes

    def veraenderungen_firma(
        self, von: date, bis: date, *, rechtsform: str = ""
    ) -> list[FirmaChange]:
        self.queried_von.append(von)
        return self.firma_changes


def ja_ref(fnr: str, stichtag: str, ext: str = "xml") -> UrkundeRef:
    return UrkundeRef(
        key=f"{fnr}|{stichtag}|{ext}",
        fnr=fnr,
        dokumentart_code="48",
        dokumentart_text="Jahresabschluss",
        dateiendung=ext,
        stichtag=stichtag,
        gkl="K",
    )
