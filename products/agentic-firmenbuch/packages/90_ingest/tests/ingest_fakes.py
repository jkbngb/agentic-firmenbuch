"""A fake RegisterSource for offline ingest tests."""

from __future__ import annotations

from datetime import date

from fbl_firmenbuch_client import (
    AuszugKurz,
    DocChange,
    FirmaChange,
    FirmaResult,
    RawResponse,
    UrkundeContent,
    UrkundeRef,
)


def legacy_xml(fnr: str, stichtag: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<UEBERMITTLUNG xmlns="https://finanzonline.bmf.gv.at/bilanz">'
        '<BILANZ_GLIEDERUNG ART="HGB"><ALLG_JUSTIZ>'
        f"<FIRMA><FNR>{fnr}</FNR></FIRMA>"
        f"<GJ><BEGINN>2023-01-01</BEGINN><ENDE>{stichtag}</ENDE></GJ><WAEHRUNG>EUR</WAEHRUNG>"
        "</ALLG_JUSTIZ><HGB_Form_2>"
        "<HGB_224_2><POSTENZEILE><BETRAG>1000.00</BETRAG></POSTENZEILE></HGB_224_2>"
        "</HGB_Form_2></BILANZ_GLIEDERUNG></UEBERMITTLUNG>"
    ).encode()


class FakeSource:
    """Configurable in-memory RegisterSource satisfying the Protocol."""

    def __init__(
        self,
        *,
        universe: dict[str, str] | None = None,  # {fnr: name}
        documents: dict[str, list[UrkundeRef]] | None = None,
        firma_changes: list[FirmaChange] | None = None,
        doc_changes: list[DocChange] | None = None,
    ) -> None:
        self.universe = universe or {}
        self.documents = documents or {}
        self.firma_changes = firma_changes or []
        self.doc_changes = doc_changes or []
        self.urkunde_calls: list[str] = []
        self.firma_calls: list[tuple[date, date]] = []  # (von, bis) of each change-feed call
        self.urkunde_windows: list[tuple[date, date]] = []
        self.auszug_calls: list[str] = []
        self.suche_firma_calls = 0

    def suche_firma(
        self,
        firmenwortlaut: str,
        *,
        suchbereich: int = 1,
        rechtsform: str = "",
        exaktesuche: bool = True,
        gericht: str = "",
        ortnr: str = "",
    ) -> list[FirmaResult]:
        self.suche_firma_calls += 1
        prefix = firmenwortlaut.rstrip("*").lower()
        out = [
            FirmaResult(fnr=fnr, name=name, rechtsform_code="GES")
            for fnr, name in sorted(self.universe.items())
            if name.lower().startswith(prefix)
        ]
        return out

    def suche_urkunde(self, fnr: str) -> list[UrkundeRef]:
        return self.documents.get(fnr, [])

    def urkunde(self, key: str) -> UrkundeContent:
        self.urkunde_calls.append(key)
        # key format: "<fnr>|<stichtag>|<ext>"
        fnr, stichtag, ext = key.split("|")
        if ext == "pdf":
            return UrkundeContent(
                key=key,
                fnr=fnr,
                content_type="application/pdf",
                dateiendung="pdf",
                content=b"%PDF-1.4 fake",
                format="pdf",
                stichtag=stichtag,
            )
        return UrkundeContent(
            key=key,
            fnr=fnr,
            content_type="application/xml",
            dateiendung="xml",
            content=legacy_xml(fnr, stichtag),
            format="legacy_finanzonline",
            stichtag=stichtag,
        )

    def auszug(self, fnr: str, *, stichtag: date | None = None) -> AuszugKurz:
        self.auszug_calls.append(fnr)
        return AuszugKurz(fnr=fnr, name=self.universe.get(fnr, "Unknown GmbH"), city="Wien")

    def veraenderungen_urkunden(self, von: date, bis: date) -> list[DocChange]:
        self.urkunde_windows.append((von, bis))
        return self.doc_changes

    def veraenderungen_firma(
        self, von: date, bis: date, *, rechtsform: str = ""
    ) -> list[FirmaChange]:
        self.firma_calls.append((von, bis))
        return self.firma_changes


class CapturingFakeSource(FakeSource):
    """A FakeSource that retains verbatim response bytes (satisfies RawCapturingSource).

    Mirrors the real client: sucheUrkunde + auszug responses are captured; urkunde
    document payloads are NOT (already byte-preserved decoded by ingest).
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._raw: list[RawResponse] = []

    def drain_raw(self) -> list[RawResponse]:
        out = self._raw
        self._raw = []
        return out

    def suche_urkunde(self, fnr: str) -> list[UrkundeRef]:
        self._raw.append(RawResponse("sucheUrkunde", f"<SUCHEURKUNDE fnr='{fnr}'/>".encode()))
        return super().suche_urkunde(fnr)

    def auszug(self, fnr: str, *, stichtag: date | None = None) -> AuszugKurz:
        self._raw.append(RawResponse("auszug_v2", f"<AUSZUG fnr='{fnr}'/>".encode()))
        return super().auszug(fnr, stichtag=stichtag)


def ja_ref(fnr: str, stichtag: str, ext: str, *, gkl: str = "K") -> UrkundeRef:
    """Build a Jahresabschluss UrkundeRef whose key encodes fnr|stichtag|ext."""
    return UrkundeRef(
        key=f"{fnr}|{stichtag}|{ext}",
        fnr=fnr,
        dokumentart_code="48",
        dokumentart_text="Jahresabschluss",
        dateiendung=ext,
        content_type="application/xml" if ext == "xml" else "application/pdf",
        stichtag=stichtag,
        gkl=gkl,
        eingereicht=f"{stichtag[:4]}-06-15",
    )
