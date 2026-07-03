"""One parsed filing — the ``70_parsed`` contract (Technische Spezifikation §6, §8.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fbl_core.models.meta import Meta

FilingFormat = Literal["legacy_finanzonline", "firmenbuch_2025", "jab40_semantic", "pdf"]
RevenueBasis = Literal["umsatzerloese", "rohergebnis"]


class Bilanz(BaseModel):
    """Canonical balance-sheet positions used by the metrics (Appendix C.1)."""

    bilanzsumme: float | None = None
    eigenkapital: float | None = None
    verbindlichkeiten: float | None = None
    anlagevermoegen: float | None = None
    umlaufvermoegen: float | None = None
    sachanlagen: float | None = None
    finanzanlagen: float | None = None
    vorraete: float | None = None
    forderungen: float | None = None
    cash: float | None = None
    rueckstellungen: float | None = None
    stammkapital: float | None = None
    kapitalruecklagen: float | None = None
    gewinnruecklagen: float | None = None
    bilanzgewinn_verlust: float | None = None


class GuV(BaseModel):
    """Canonical income-statement positions (Appendix C.1).

    ``umsatzerloese`` (§231 full disclosure) and ``rohergebnis`` (§279
    alternative) are DISTINCT and never conflated; ``revenue_basis`` records
    which one the company published.
    """

    revenue_basis: RevenueBasis | None = None
    umsatzerloese: float | None = None
    rohergebnis: float | None = None
    materialaufwand: float | None = None
    personalaufwand: float | None = None
    abschreibungen: float | None = None
    ebit: float | None = None
    ebitda: float | None = None
    # operating_result is the Betriebserfolg (§231 Abs 2) under its correct name — identical
    # value to ``ebit``, which is kept as a documented alias. ebit_strict is TRUE EBIT
    # (Ergebnis vor Steuern + Zinsaufwand); null when the GuV lacks those lines (#6).
    operating_result: float | None = None
    ebit_strict: float | None = None
    jahresueberschuss: float | None = None


class Signatory(BaseModel):
    """A signing officer. Name is gated downstream; age/birth_year are exposed.

    The raw filing carries a full ``GEB_DAT`` (birth DATE). Parse computes
    ``age_at_signing`` and derives ``birth_year``, then DISCARDS day/month —
    only the year and the age are ever retained (GDPR, §5/§8.7).
    """

    first_name: str | None = None  # parsed internally, NOT served publicly
    last_name: str | None = None  # parsed internally, NOT served publicly
    birth_year: int | None = None  # YEAR ONLY — never store/serve full date
    age_at_signing: float | None = None  # (signature_date - birth_date)/365.25, 1 decimal
    signed_at: str | None = None  # signature_date (DAT_UNT)
    role_code: str | None = None  # PERS_KENN (A/B/C/D)


class FieldProvenance(BaseModel):
    """Parse-stage provenance: how each canonical field was sourced (§7)."""

    format: FilingFormat
    mapping_version: str = "1.0"
    scaling: dict[str, bool] = Field(default_factory=dict)  # {"wert_tsd_applied": bool}
    map: dict[str, str] = Field(default_factory=dict)  # canonical_field -> source XML path
    passthrough: dict[str, float] = Field(default_factory=dict)  # unknown codes -> value


class ParsedFiling(BaseModel):
    """The canonical normalized form of a single Jahresabschluss filing."""

    fnr: str
    stichtag: str  # "YYYY-MM-DD"
    name: str | None = None  # company name as carried in the filing (§15b-5), if present
    legal_form: str | None = None  # Rechtsform carried in the filing (jab40 RECHTSFORM), if any
    gj_beginn: str | None = None
    gj_ende: str | None = None
    currency: str = "EUR"
    gkl: str | None = None  # size class from the filing (EINSTUFUNG: K/M/G; W=Mikro)
    format: FilingFormat
    parsed: bool
    has_bilanz: bool = False
    has_guv: bool = False
    bilanz: Bilanz = Field(default_factory=Bilanz)
    guv: GuV | None = None
    # All recognized canonical positions for this filing (full taxonomy, not just
    # the typed Bilanz/GuV subset) so consolidate can build a series for every line
    # item and nothing recognized is lost in the projection (§5.1).
    positions: dict[str, float] = Field(default_factory=dict)
    # Prior-year column (BETRAG_VJ) per canonical — for prior-year reconciliation only,
    # never a value of record (§15b-8).
    positions_prior_year: dict[str, float] = Field(default_factory=dict)
    # Official source code(s) each canonical was parsed from — HGB_*/XXX_* code or JAb 4.0
    # element name. Two distinct codes mapping to one canonical are BOTH kept (§-traceability).
    position_codes: dict[str, list[str]] = Field(default_factory=dict)
    employees: int | None = None
    signatory: Signatory | None = None
    signatories: list[Signatory] = Field(default_factory=list)
    error: str | None = None  # set on dead-letter stub (parse failure)
    field_provenance: FieldProvenance
    meta: Meta
