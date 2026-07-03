"""Company-level contracts: consolidated → derived → presented (§6).

These share a base. ``consolidated`` holds facts (per-line histories, no growth);
``derived`` adds ratios/growth/percentiles; ``presented`` is the gated public view
(assembled in the present stage, see ``mcp.py`` for the served envelope shapes).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fbl_core.models.meta import Meta
from fbl_core.models.metric import MetricSeries

LegalStatus = Literal["active", "historical", "deleted"]
SizeClass = Literal["W", "K", "M", "G"]  # Mikro/Kleinst, Klein, Mittel, Groß


class Court(BaseModel):
    code: str | None = None
    name: str | None = None


class Identity(BaseModel):
    fnr: str
    register_id: str | None = None
    name: str
    legal_form: str | None = None
    status: LegalStatus = "active"
    court: Court | None = None


class Location(BaseModel):
    country: str = "AT"
    bundesland: str | None = None
    city: str | None = None
    postal_code: str | None = None
    street: str | None = None


class Money(BaseModel):
    amount: float | None = None
    currency: str = "EUR"


class CompanyMaster(BaseModel):
    stammkapital: Money | None = None
    first_filing_year: int | None = None
    last_filing_year: int | None = None
    filing_years_available: int | None = None
    founded_year: int | None = None
    founded_source: str | None = None
    description: str | None = None


class Size(BaseModel):
    gkl: SizeClass | None = None  # UGB §221 Größenklasse from the filing (W/K/M/G)
    # Size proxy bucketed purely by Bilanzsumme thresholds (micro/small/medium/large/
    # very_large) — a DIFFERENT axis from `gkl`, so e.g. gkl="K" can sit next to
    # bilanzsumme_band="medium" without contradiction. Renamed from `band` for clarity.
    bilanzsumme_band: str | None = None
    peer_percentiles: dict[str, float] = Field(default_factory=dict)


class Financials(BaseModel):
    currency: str = "EUR"
    latest_year: int | None = None
    has_bilanz: bool = False
    has_guv: bool = False
    has_guv_latest: bool = False
    guv_years: list[int] = Field(default_factory=list)
    has_xml: bool = True
    has_pdf_only: bool = False
    revenue_basis: str | None = None
    completeness: dict[int, dict[str, int]] = Field(default_factory=dict)
    bilanz: dict[str, MetricSeries] = Field(default_factory=dict)
    guv: dict[str, MetricSeries] = Field(default_factory=dict)
    # Strict no-loss superset (Part B): EVERY recognized canonical position (full 317-entry
    # taxonomy), keyed by canonical, with its complete year history — not just the typed
    # Bilanz/GuV subset above. `passthrough` keeps every UNKNOWN source code's history too,
    # so nothing recognized OR unrecognized is reduced on the way up (§5.1).
    positions: dict[str, MetricSeries] = Field(default_factory=dict)
    passthrough: dict[str, MetricSeries] = Field(default_factory=dict)


class Manager(BaseModel):
    first_name: str | None = None  # internal only; gated at present
    last_name: str | None = None  # internal only; gated at present
    birth_year: int | None = None
    age_at_signing: float | None = None
    role_label: str | None = None  # function/role, e.g. "GESCHÄFTSFÜHRER/IN"
    vertretung: str | None = (
        None  # Vertretungsart, e.g. "Einzelvertretung" (sole signing authority)
    )


class Management(BaseModel):
    primary_gf: Manager | None = None
    n_signatories_latest: int | None = None
    signatories_stable_years: int | None = None
    signatories_history: MetricSeries | None = None


class FilingRef(BaseModel):
    stichtag: str
    format: str
    parsed: bool
    gkl: str | None = None
    eingereicht: str | None = None
    doc_key: str | None = None
    document_url: str | None = None
    pdf_doc_key: str | None = None


class RegisterEvent(BaseModel):
    date: str
    type: str
    description: str | None = None
    # Provenance. "change_feed_delta" = derived by diffing the daily change-feed master snapshot
    # against the prior one (issue #16): the HVD tier does not return the historical VOLLZ log, so
    # events are derived, not read. "auszug" = a literal VOLLZ entry (rare on this tier).
    source: str | None = None


class Ratios(BaseModel):
    equity_ratio: MetricSeries | None = None
    debt_ratio: MetricSeries | None = None
    debt_to_equity: MetricSeries | None = None
    working_capital_ratio: MetricSeries | None = None
    anlagedeckungsgrad_1: MetricSeries | None = None
    ebit_margin: MetricSeries | None = None  # operating-result basis (Betriebserfolg); see ebit
    ebit_strict_margin: MetricSeries | None = None  # true-EBIT basis; null when ebit_strict is (#6)
    ebitda_margin: MetricSeries | None = None
    net_margin: MetricSeries | None = None
    personalkostenquote: MetricSeries | None = None
    materialaufwandsquote: MetricSeries | None = None
    roa: MetricSeries | None = None
    roe: MetricSeries | None = None
    capital_profile: str | None = None


class Growth(BaseModel):
    profile: str | None = None
    method: str | None = None


class Derivations(BaseModel):
    metrics_version: str = "1.0"
    formulas: dict[str, str] = Field(default_factory=dict)


class MasterData(BaseModel):
    """Canonical master data for a company (from ``auszug``; decoupled from the API DTO).

    Persons keep birth YEAR only (§8.7). Consumed by ``consolidate``; produced from the
    client's ``AuszugKurz`` so the consolidate stage need not import the API adapter.
    """

    fnr: str
    name: str | None = None
    legal_form: str | None = None
    status: LegalStatus | None = None
    court: Court | None = None
    location: Location | None = None
    stammkapital: Money | None = None
    founded_year: int | None = None
    description: str | None = None
    persons: list[Manager] = Field(default_factory=list)
    events: list[RegisterEvent] = Field(default_factory=list)


class ConsolidatedCompany(BaseModel):
    identity: Identity
    location: Location
    company: CompanyMaster
    size: Size
    financials: Financials
    employees: MetricSeries | None = None
    management: Management | None = None  # gated at present
    filings: list[FilingRef] = Field(default_factory=list)
    events: list[RegisterEvent] = Field(default_factory=list)
    # Internal: the master-data snapshot the events were derived against (issue #16). Compared
    # against the next delta's master to surface what changed. Never served — present() whitelists
    # fields, and get_full_record's _strip_internal drops it.
    event_baseline: dict[str, object] | None = None
    # reserved (None in v1)
    sector: None = None
    enrichment: None = None
    score: None = None
    summary: None = None
    observations: None = None
    meta: Meta


class DerivedCompany(ConsolidatedCompany):
    ratios: Ratios
    growth: Growth
    derivations: Derivations
