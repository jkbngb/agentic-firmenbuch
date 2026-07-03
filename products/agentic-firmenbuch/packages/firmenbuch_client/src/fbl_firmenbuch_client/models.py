"""Typed results for the six HVD API calls (§8.2).

These are adapter-level DTOs (decoupled from the canonical pipeline models in
``fbl_core``). Personal data is minimized at this boundary: ``auszug`` persons keep
only ``birth_year`` (day/month discarded), consistent with §8.7.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fbl_core.austria import bundesland_from_plz
from fbl_core.models.company import Court, Location, Manager, MasterData, Money, RegisterEvent
from fbl_core.models.filing import FilingFormat

DOKUMENTART_JAHRESABSCHLUSS = "48"


def normalize_fnr(raw: str) -> str:
    """Normalize an FNR to the canonical zero-padded form, e.g. ``"30435 h"`` → ``"030435h"``.

    Responses use several spellings (``030435h`` / ``30435 h`` / ``30435h``). The
    registry/key form is 6 numeric digits plus the check letter, no spaces.
    """
    compact = raw.replace(" ", "").strip()
    if not compact:
        return compact
    digits = "".join(c for c in compact if c.isdigit())
    suffix = (
        compact[len(digits) :] if compact[: len(digits)] == digits else compact.lstrip("0123456789")
    )
    # Pad the numeric part to 6 digits when shorter (FNRs are <= 6 digits).
    if digits and len(digits) < 6:
        digits = digits.zfill(6)
    return f"{digits}{suffix}"


class FirmaResult(BaseModel):
    """One company hit from ``sucheFirma``."""

    fnr: str
    status: str | None = None  # "", "historisch", "gelöscht"
    name: str | None = None
    sitz: str | None = None
    rechtsform_code: str | None = None
    rechtsform_text: str | None = None
    gericht_code: str | None = None
    gericht_text: str | None = None


class UrkundeRef(BaseModel):
    """One document listing from ``sucheUrkunde``."""

    key: str
    fnr: str | None = None
    az: str | None = None
    dokumentart_code: str | None = None
    dokumentart_text: str | None = None
    content_type: str | None = None
    dateiendung: str | None = None
    groesse: int | None = None
    stichtag: str | None = None
    gkl: str | None = None
    eingereicht: str | None = None
    dokumentendatum: str | None = None
    bemerkung: str | None = None

    @property
    def is_jahresabschluss(self) -> bool:
        return self.dokumentart_code == DOKUMENTART_JAHRESABSCHLUSS

    @property
    def is_xml(self) -> bool:
        return (self.dateiendung or "").lower() == "xml"


class UrkundeContent(BaseModel):
    """Downloaded document bytes + detected format from ``urkunde``."""

    key: str
    fnr: str | None = None
    content_type: str | None = None
    dateiendung: str | None = None
    content: bytes
    format: FilingFormat
    stichtag: str | None = None
    gkl: str | None = None
    oeffentlich: bool | None = None


class AuszugPerson(BaseModel):
    """A person from ``auszug`` — name gated downstream, birth YEAR only (§8.7)."""

    first_name: str | None = None
    last_name: str | None = None
    birth_year: int | None = None  # day/month discarded at this boundary
    function_code: str | None = None
    function_text: str | None = None
    vertretung: str | None = None  # Vertretungsart, e.g. "Einzelvertretung"


class RegistrationEvent(BaseModel):
    """A VOLLZ entry from ``auszug`` (registry event)."""

    vnr: str | None = None
    date: str | None = None
    court_code: str | None = None
    court_text: str | None = None
    az: str | None = None
    text: str | None = None
    received_at: str | None = None


class AuszugKurz(BaseModel):
    """Master data (Kurzinformation) from ``auszug`` — confirmed available (§16)."""

    fnr: str
    name: str | None = None
    street: str | None = None
    house_number: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    sitz: str | None = None
    geschaeftszweig: str | None = None
    rechtsform_code: str | None = None
    rechtsform_text: str | None = None
    court_code: str | None = None  # Firmenbuchgericht (HG) — code, e.g. "818"
    court_text: str | None = None  # e.g. "Landesgericht Innsbruck"
    stammkapital: float | None = None
    currency: str | None = None
    euid: str | None = None
    persons: list[AuszugPerson] = Field(default_factory=list)
    events: list[RegistrationEvent] = Field(default_factory=list)

    def to_master_data(self) -> MasterData:
        """Map this API DTO to the canonical core ``MasterData`` (decouples consolidate)."""
        return MasterData(
            fnr=self.fnr,
            name=self.name,
            legal_form=self.rechtsform_code,
            court=(
                Court(code=self.court_code, name=self.court_text)
                if (self.court_code or self.court_text)
                else None
            ),
            location=Location(
                country=self.country or "AT",
                bundesland=bundesland_from_plz(self.postal_code),
                city=self.city,
                postal_code=self.postal_code,
                street=_join_street(self.street, self.house_number),
            ),
            stammkapital=(
                Money(amount=self.stammkapital, currency=self.currency or "EUR")
                if self.stammkapital is not None
                else None
            ),
            description=self.geschaeftszweig,
            persons=[
                Manager(
                    first_name=p.first_name,
                    last_name=p.last_name,
                    birth_year=p.birth_year,
                    role_label=p.function_text,
                    vertretung=p.vertretung,
                )
                for p in self.persons
            ],
            events=[
                RegisterEvent(date=e.date or "", type="registry_event", description=e.text)
                for e in self.events
                if e.date
            ],
        )


def _join_street(street: str | None, house_number: str | None) -> str | None:
    parts = [p for p in (street, house_number) if p]
    return " ".join(parts) if parts else None


class DocChange(BaseModel):
    """A document change from ``veraenderungenUrkunden``."""

    key: str
    fnr: str | None = None
    dokumentart_code: str | None = None
    dokumentart_text: str | None = None
    vollzugsdatum: str | None = None
    content_type: str | None = None
    dateiendung: str | None = None


FirmaChangeKind = Literal["Neueintragung", "Änderung", "Löschung", "other"]


class FirmaChange(BaseModel):
    """A register change from ``veraenderungenFirma``."""

    fnr: str
    vnr: str | None = None
    vollzugsdatum: str | None = None
    art: str | None = None  # raw ARTDERVERAENDERUNG

    @property
    def kind(self) -> FirmaChangeKind:
        if self.art in ("Neueintragung", "Änderung", "Löschung"):
            return self.art  # type: ignore[return-value]
        return "other"
