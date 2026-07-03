"""``JustizOnlineClient`` — concrete ``RegisterSource`` over the HVD SOAP API (§8.2).

Auth is the ``X-API-KEY`` header (confirmed live). Read-only. HTTP 429/5xx are
retried with exponential backoff; faults and exhausted retries raise
``FirmenbuchApiError`` so ingest can dead-letter one company without failing the run.
SOAP request element order is schema-enforced (see docs/API_PROBE_FINDINGS.md).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from datetime import date

import httpx
from lxml import etree

from fbl_core_at.formats import detect_xml_variant_bytes
from fbl_core_at.models.filing import FilingFormat

from .envelope import build_envelope, child_text, direct_child, fault_string, iter_named
from .errors import FirmenbuchApiError
from .models import (
    AuszugKurz,
    AuszugPerson,
    DocChange,
    FirmaChange,
    FirmaResult,
    RegistrationEvent,
    UrkundeContent,
    UrkundeRef,
    normalize_fnr,
)
from .source import RawResponse, RegisterSource

_ABFRAGE = "ns://firmenbuch.justiz.gv.at/Abfrage"
_REQUEST_NS = {
    "sucheFirma": f"{_ABFRAGE}/SucheFirmaRequest",
    "sucheUrkunde": f"{_ABFRAGE}/SucheUrkundeRequest",
    "urkunde": f"{_ABFRAGE}/UrkundeRequest",
    "auszug_v2": f"{_ABFRAGE}/v2/AuszugRequest",
    "veraenderungenUrkunden": f"{_ABFRAGE}/VeraenderungenUrkundeRequest",
    "veraenderungenFirma": f"{_ABFRAGE}/VeraenderungenFirmaRequest",
}
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _esc(value: str) -> str:
    """Minimal XML escaping for request parameter values."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fnr_from_key(key: str) -> str | None:
    """Extract and normalize the FNR embedded in a document KEY (``030435_...``)."""
    head = key.split("_", 1)[0]
    return normalize_fnr(head) if head.isdigit() else None


class JustizOnlineClient(RegisterSource):
    """Hardened SOAP client for the Firmenbuch HVD API."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 4,
        backoff_base: float = 0.5,
        timeout: float = 120.0,
        connect_timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        capture_raw: bool = True,
    ) -> None:
        self._base = api_url.rstrip("/")
        self._key = api_key
        # Granular timeout: a SHORT connect timeout fails fast on an unresponsive host, but a
        # GENEROUS read timeout lets a large urkunde (a bank/insurer PDF the server generates
        # on demand → long time-to-first-byte, then a multi-MB body) finish. A single flat
        # value can't do both — the old flat 20 s tripped on TTFB and dead-lettered the biggest
        # filings (ROADMAP P1.2). httpx resets the read timeout per chunk, so a steadily
        # streaming body never trips it; only a genuinely hung connection does.
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=connect_timeout)
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._capture_raw = capture_raw
        self._raw: list[RawResponse] = []  # verbatim responses pending archival (§5.1)

    def drain_raw(self) -> list[RawResponse]:
        """Return captured responses since the last drain and clear the buffer (§5.1)."""
        out = self._raw
        self._raw = []
        return out

    # --- transport -----------------------------------------------------------

    def _post(self, endpoint: str, body_inner: str) -> etree._Element:
        url = f"{self._base}/{endpoint}"
        payload = build_envelope(_REQUEST_NS[endpoint], body_inner)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "X-API-KEY": self._key,
            "SOAPAction": "",
        }
        last_error = "no attempt made"
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.post(url, content=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"transport: {exc}"
            else:
                # A SOAP Fault is deterministic (e.g. a schema validation error
                # served as HTTP 500) — raise immediately rather than retrying.
                root = _try_parse(resp.content)
                if root is not None:
                    fault = fault_string(root)
                    if fault is not None:
                        raise FirmenbuchApiError(
                            f"{endpoint}: {fault}", status=resp.status_code, endpoint=endpoint
                        )
                    if resp.status_code < 400:
                        # Retain the verbatim response for lossless archival (§5.1).
                        # ``urkunde`` is excluded: its document payload is already
                        # byte-preserved (decoded) by ingest, so capturing the base64
                        # SOAP envelope here would only double the storage.
                        if self._capture_raw and endpoint != "urkunde":
                            self._raw.append(RawResponse(endpoint, resp.content))
                        return root
                if resp.status_code not in _RETRYABLE_STATUS and resp.status_code >= 400:
                    raise FirmenbuchApiError(
                        f"{endpoint}: http {resp.status_code}",
                        status=resp.status_code,
                        endpoint=endpoint,
                    )
                last_error = f"http {resp.status_code}"
            if attempt < self._max_retries:
                self._sleep(self._backoff_base * (2**attempt))
        raise FirmenbuchApiError(
            f"{endpoint} failed after {self._max_retries + 1} attempts: {last_error}",
            endpoint=endpoint,
        )

    # --- calls ---------------------------------------------------------------

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
        # Element ORDER is schema-enforced: WORTLAUT, EXAKTESUCHE, SUCHBEREICH,
        # GERICHT, RECHTSFORM, RECHTSEIGENSCHAFT, ORTNR.
        body = (
            "<fb:SUCHEFIRMAREQUEST>"
            f"<fb:FIRMENWORTLAUT>{_esc(firmenwortlaut)}</fb:FIRMENWORTLAUT>"
            f"<fb:EXAKTESUCHE>{'true' if exaktesuche else 'false'}</fb:EXAKTESUCHE>"
            f"<fb:SUCHBEREICH>{suchbereich}</fb:SUCHBEREICH>"
            f"<fb:GERICHT>{_esc(gericht)}</fb:GERICHT>"
            f"<fb:RECHTSFORM>{_esc(rechtsform)}</fb:RECHTSFORM>"
            "<fb:RECHTSEIGENSCHAFT></fb:RECHTSEIGENSCHAFT>"
            f"<fb:ORTNR>{_esc(ortnr)}</fb:ORTNR>"
            "</fb:SUCHEFIRMAREQUEST>"
        )
        root = self._post("sucheFirma", body)
        return [self._parse_firma(e) for e in iter_named(root, "ERGEBNIS")]

    def suche_urkunde(self, fnr: str) -> list[UrkundeRef]:
        body = f"<fb:SUCHEURKUNDEREQUEST><fb:FNR>{_esc(fnr)}</fb:FNR></fb:SUCHEURKUNDEREQUEST>"
        root = self._post("sucheUrkunde", body)
        return [self._parse_urkunde_ref(e) for e in iter_named(root, "ERGEBNIS")]

    def urkunde(self, key: str) -> UrkundeContent:
        body = f"<fb:URKUNDEREQUEST><fb:KEY>{_esc(key)}</fb:KEY></fb:URKUNDEREQUEST>"
        root = self._post("urkunde", body)
        return self._parse_urkunde_content(root, key)

    def auszug(self, fnr: str, *, stichtag: date | None = None) -> AuszugKurz:
        day = (stichtag or date.today()).isoformat()
        body = (
            "<fb:AUSZUG_V2_REQUEST>"
            f"<fb:FNR>{_esc(fnr)}</fb:FNR>"
            f"<fb:STICHTAG>{day}</fb:STICHTAG>"
            "<fb:UMFANG>Kurzinformation</fb:UMFANG>"
            "</fb:AUSZUG_V2_REQUEST>"
        )
        root = self._post("auszug_v2", body)
        return self._parse_auszug(root, fnr)

    def veraenderungen_urkunden(self, von: date, bis: date) -> list[DocChange]:
        body = (
            "<fb:VERAENDERUNGENURKUNDEREQUEST>"
            f"<fb:VON>{von.isoformat()}</fb:VON><fb:BIS>{bis.isoformat()}</fb:BIS>"
            "</fb:VERAENDERUNGENURKUNDEREQUEST>"
        )
        root = self._post("veraenderungenUrkunden", body)
        return [self._parse_doc_change(e) for e in iter_named(root, "VERAENDERUNG")]

    def veraenderungen_firma(
        self, von: date, bis: date, *, rechtsform: str = ""
    ) -> list[FirmaChange]:
        body = (
            "<fb:VERAENDERUNGENFIRMAREQUEST>"
            f"<fb:VON>{von.isoformat()}</fb:VON><fb:BIS>{bis.isoformat()}</fb:BIS>"
            f"<fb:RECHTSFORM>{_esc(rechtsform)}</fb:RECHTSFORM>"
            "</fb:VERAENDERUNGENFIRMAREQUEST>"
        )
        root = self._post("veraenderungenFirma", body)
        return [self._parse_firma_change(e) for e in iter_named(root, "VERAENDERUNG")]

    # --- parsers -------------------------------------------------------------

    @staticmethod
    def _code_text(parent: etree._Element, name: str) -> tuple[str | None, str | None]:
        node = direct_child(parent, name)
        if node is None:
            return None, None
        return child_text(node, "CODE"), child_text(node, "TEXT")

    def _parse_firma(self, e: etree._Element) -> FirmaResult:
        rf_code, rf_text = self._code_text(e, "RECHTSFORM")
        g_code, g_text = self._code_text(e, "GERICHT")
        fnr = child_text(e, "FNR") or ""
        return FirmaResult(
            fnr=normalize_fnr(fnr),
            status=child_text(e, "STATUS"),
            name=child_text(e, "NAME"),
            sitz=child_text(e, "SITZ"),
            rechtsform_code=rf_code,
            rechtsform_text=rf_text,
            gericht_code=g_code,
            gericht_text=g_text,
        )

    def _parse_urkunde_ref(self, e: etree._Element) -> UrkundeRef:
        art_code, art_text = self._code_text(e, "DOKUMENTART")
        groesse = child_text(e, "GROESSE")
        fnr = child_text(e, "FNR")
        return UrkundeRef(
            key=child_text(e, "KEY") or "",
            fnr=normalize_fnr(fnr) if fnr else None,
            az=child_text(e, "AZ"),
            dokumentart_code=art_code,
            dokumentart_text=art_text,
            content_type=child_text(e, "CONTENTTYPE"),
            dateiendung=child_text(e, "DATEIENDUNG"),
            groesse=int(groesse) if groesse and groesse.isdigit() else None,
            stichtag=child_text(e, "STICHTAG"),
            gkl=child_text(e, "GKL"),
            eingereicht=child_text(e, "EINGEREICHT"),
            dokumentendatum=child_text(e, "DOKUMENTENDATUM"),
            bemerkung=child_text(e, "BEMERKUNG"),
        )

    def _parse_urkunde_content(self, root: etree._Element, key: str) -> UrkundeContent:
        doc = None
        for e in iter_named(root, "DOKUMENT"):
            doc = e
            break
        content_b64 = child_text(doc, "CONTENT") if doc is not None else None
        content = base64.b64decode(content_b64) if content_b64 else b""
        content_type = child_text(doc, "CONTENTTYPE") if doc is not None else None
        dateiendung = child_text(doc, "DATEIENDUNG") if doc is not None else None
        fnr = child_text(root, "FNR")
        return UrkundeContent(
            key=key,
            fnr=normalize_fnr(fnr) if fnr else _fnr_from_key(key),
            content_type=content_type,
            dateiendung=dateiendung,
            content=content,
            format=self._detect_format(content_type, dateiendung, content),
            stichtag=child_text(root, "STICHTAG"),
            gkl=child_text(root, "GKL"),
            oeffentlich=_to_bool(child_text(root, "OEFFENTLICH")),
        )

    @staticmethod
    def _detect_format(
        content_type: str | None, dateiendung: str | None, content: bytes
    ) -> FilingFormat:
        ct = (content_type or "").lower()
        ext = (dateiendung or "").lower()
        if "pdf" in ct or ext == "pdf":
            return "pdf"
        if not content:
            return "pdf"
        try:
            return detect_xml_variant_bytes(content)
        except etree.XMLSyntaxError:
            return "pdf"

    def _parse_auszug(self, root: etree._Element, fnr: str) -> AuszugKurz:
        kapital = child_text(root, "KAPITAL")
        # Join the FUN role blocks (FKEN/FKENTEXT, e.g. GF "Geschäftsführer") to persons
        # by PNR, so each person carries its function/role (§5.1 — role was being dropped).
        roles = self._function_roles(root)
        persons = [self._parse_person(p, roles) for p in iter_named(root, "PER")]
        events = [self._parse_event(v) for v in iter_named(root, "VOLLZ")]
        rf_code, rf_text = self._code_text_deep(root, "RECHTSFORM")
        court_code, court_text = self._code_text_deep(root, "HG")  # Firmenbuchgericht (§5.1)
        return AuszugKurz(
            fnr=normalize_fnr(fnr),
            name=child_text(root, "BEZEICHNUNG"),
            court_code=court_code,
            court_text=court_text,
            street=child_text(root, "STRASSE"),
            house_number=child_text(root, "HAUSNUMMER"),
            postal_code=child_text(root, "PLZ"),
            city=child_text(root, "ORT"),
            country=child_text(root, "STAAT"),
            sitz=child_text(root, "SITZ"),
            geschaeftszweig=self._geschaeftszweig(root),
            rechtsform_code=rf_code,
            rechtsform_text=rf_text,
            stammkapital=float(kapital) if kapital and _is_number(kapital) else None,
            currency=child_text(root, "WHR"),
            euid=self._euid(root),
            persons=persons,
            events=events,
        )

    @staticmethod
    def _attr_local(el: etree._Element, name: str) -> str | None:
        """Attribute value by local name, ignoring the XML namespace prefix."""
        for key, value in el.attrib.items():
            if str(key).split("}")[-1] == name:
                return str(value)
        return None

    def _function_roles(
        self, root: etree._Element
    ) -> dict[str, tuple[str | None, str | None, str | None]]:
        """Map PNR → (FKEN code, FKENTEXT, Vertretungsart) from the FUN function blocks.

        Vertretungsart (VART/TEXT, e.g. "Einzelvertretung" = sole signing authority) is a
        governance signal — who can bind the company — so it is carried, not dropped.
        """
        roles: dict[str, tuple[str | None, str | None, str | None]] = {}
        for fun in iter_named(root, "FUN"):
            pnr = (self._attr_local(fun, "PNR") or "").strip()
            fken, fkentext = self._attr_local(fun, "FKEN"), self._attr_local(fun, "FKENTEXT")
            vart = None
            for v in iter_named(fun, "VART"):
                vart = child_text(v, "TEXT")
                break
            if pnr and (fken or fkentext or vart):
                roles.setdefault(pnr, (fken, fkentext, vart))
        return roles

    @staticmethod
    def _euid(root: etree._Element) -> str | None:
        """The EUID value — nested ``<EUID><EUID>ATBRA…</EUID></EUID>``, not the container."""
        for container in iter_named(root, "EUID"):
            for child in container:
                if isinstance(child.tag, str) and child.tag.split("}")[-1] == "EUID":
                    text = (child.text or "").strip()
                    if text:
                        return text
        return None

    @staticmethod
    def _geschaeftszweig(root: etree._Element) -> str | None:
        # FI_DKZ05 holds the business purpose as a TEXT leaf.
        for e in iter_named(root, "FI_DKZ05"):
            return child_text(e, "TEXT")
        return None

    @staticmethod
    def _code_text_deep(root: etree._Element, name: str) -> tuple[str | None, str | None]:
        for node in iter_named(root, name):
            return child_text(node, "CODE"), child_text(node, "TEXT")
        return None, None

    @classmethod
    def _parse_person(
        cls, per: etree._Element, roles: dict[str, tuple[str | None, str | None, str | None]]
    ) -> AuszugPerson:
        geb = child_text(per, "GEBURTSDATUM")  # YYYYMMDD
        birth_year = int(geb[:4]) if geb and len(geb) >= 4 and geb[:4].isdigit() else None
        pnr = (cls._attr_local(per, "PNR") or "").strip()
        fken, fkentext, vart = roles.get(pnr, (None, None, None))
        return AuszugPerson(
            first_name=child_text(per, "VORNAME"),
            last_name=child_text(per, "NACHNAME"),
            birth_year=birth_year,  # day/month discarded (§8.7)
            function_code=fken,
            function_text=fkentext,
            vertretung=vart,
        )

    @staticmethod
    def _parse_event(vollz: etree._Element) -> RegistrationEvent:
        hg_code = hg_text = None
        hg = direct_child(vollz, "HG")
        if hg is not None:
            hg_code, hg_text = child_text(hg, "CODE"), child_text(hg, "TEXT")
        return RegistrationEvent(
            vnr=child_text(vollz, "VNR"),
            date=child_text(vollz, "VOLLZUGSDATUM"),
            court_code=hg_code,
            court_text=hg_text,
            az=child_text(vollz, "AZ"),
            text=child_text(vollz, "ANTRAGSTEXT"),
            received_at=child_text(vollz, "EINGELANGTAM"),
        )

    def _parse_doc_change(self, e: etree._Element) -> DocChange:
        art_code, art_text = self._code_text(e, "DOKUMENTART")
        key = child_text(e, "KEY") or ""
        return DocChange(
            key=key,
            fnr=_fnr_from_key(key),
            dokumentart_code=art_code,
            dokumentart_text=art_text,
            vollzugsdatum=child_text(e, "VOLLZUGSDATUM"),
            content_type=child_text(e, "CONTENTTYPE"),
            dateiendung=child_text(e, "DATEIENDUNG"),
        )

    @staticmethod
    def _parse_firma_change(e: etree._Element) -> FirmaChange:
        fnr = child_text(e, "FNR") or ""
        return FirmaChange(
            fnr=normalize_fnr(fnr),
            vnr=child_text(e, "VNR"),
            vollzugsdatum=child_text(e, "VOLLZUGSDATUM"),
            art=child_text(e, "ARTDERVERAENDERUNG"),
        )


def _new_parser() -> etree.XMLParser:
    """A parser that accepts the large ``urkunde`` responses.

    ``huge_tree=True`` lifts libxml2's ~10 MB single-text-node ceiling: a bank/insurer
    Jahresabschluss PDF arrives base64-encoded as ONE giant text node inside the SOAP
    envelope, which the default parser rejects with an ``XMLSyntaxError`` ("huge text
    node"). That rejection used to surface — misleadingly — as ``urkunde failed … http
    200`` (the HTTP fetch succeeded; only the parse blew up), dead-lettering ~38 % of the
    largest filings (ROADMAP P1.2). We still keep the XXE guards on (``resolve_entities``
    off, ``no_network``), since lifting the size cap must not also open entity expansion.
    A fresh parser per call keeps this thread-safe under the multi-worker backfill (libxml2
    parsers are not safe to share across threads)."""
    return etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)


def _try_parse(content: bytes) -> etree._Element | None:
    """Parse response bytes, or None if not well-formed XML (e.g. a 429 text body)."""
    try:
        return etree.fromstring(content, parser=_new_parser())
    except etree.XMLSyntaxError:
        return None


def _to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() == "true"


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
