"""MCP tool I/O contracts (Technische Spezifikation §9)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .meta import Meta


class PublicProvenance(BaseModel):
    """Trimmed public provenance attached to every served document/response."""

    # The en dash is part of the official attribution wording (CC BY 4.0).
    source: str = "Österreichisches Firmenbuch / BMJ – Justiz"
    license: str = "CC-BY-4.0"
    attribution: str = "Quelle: Österreichisches Firmenbuch / BMJ – Justiz, CC BY 4.0"
    data_version: int | None = None
    built_at: str | None = None
    schema_version: str = "1.0"


class PresentedManager(BaseModel):
    """Primary manager as served: GDPR-gated — NO name, month, or day (§8.7)."""

    age_at_signing: float | None = None
    age: int | None = None  # current age (year-of-birth based)
    birth_year: int | None = None  # YEAR ONLY
    role_label: str | None = None
    vertretung: str | None = None  # Vertretungsart (sole vs joint signing authority)


class PresentedManagement(BaseModel):
    n_signatories_latest: int | None = None
    signatories_stable_years: int | None = None
    primary_manager: PresentedManager | None = None
    # Names are exposed ONLY when expose_personal_data=True (a documented lawful basis).
    primary_manager_name: str | None = None


class PresentedFinancials(BaseModel):
    latest_year: int | None = None
    currency: str = "EUR"
    has_bilanz: bool = False
    has_guv: bool = False
    has_guv_latest: bool = False
    has_xml: bool = True
    has_pdf_only: bool = False
    revenue_basis: str | None = None
    # Denormalized shallow paths for cheap indexed queries (§4.1).
    latest: dict[str, float] = Field(default_factory=dict)
    bilanz: dict[str, object] = Field(default_factory=dict)
    guv: dict[str, object] = Field(default_factory=dict)


class PresentedCompany(BaseModel):
    """The public served document (§8.7). ``id == fnr``; internal hash chain omitted."""

    id: str
    fnr: str
    schema_version: str = "1.0"
    identity: dict[str, object] = Field(default_factory=dict)
    location: dict[str, object] = Field(default_factory=dict)
    company: dict[str, object] = Field(default_factory=dict)
    size: dict[str, object] = Field(default_factory=dict)
    financials: PresentedFinancials = Field(default_factory=PresentedFinancials)
    ratios: dict[str, object] = Field(default_factory=dict)
    growth: dict[str, object] = Field(default_factory=dict)
    employees: dict[str, object] | None = None
    filings: list[dict[str, object]] = Field(default_factory=list)
    events: list[dict[str, object]] = Field(default_factory=list)
    management: PresentedManagement | None = None
    # reserved (null in v1)
    sector: None = None
    enrichment: None = None
    score: None = None
    summary: None = None
    observations: None = None
    provenance: PublicProvenance = Field(default_factory=PublicProvenance)
    # Stored in Cosmos for lineage/idempotency; the MCP server omits it when serving.
    meta: Meta | None = None


class SearchFilters(BaseModel):
    status: Literal["active", "inactive", "all"] = "all"
    name: str | None = None  # case-insensitive substring match on the company name
    legal_form: str | None = None
    bundesland: str | None = None
    size_gkl: Literal["W", "K", "M", "G"] | None = None
    bilanzsumme_min: float | None = None
    bilanzsumme_max: float | None = None
    equity_ratio_min: float | None = None
    equity_ratio_max: float | None = None
    revenue_min: float | None = None
    revenue_max: float | None = None
    employees_min: int | None = None
    employees_max: int | None = None
    growth_profile: Literal["shrinking", "stable", "growing", "fast_growing"] | None = None
    has_guv: bool | None = None
    has_guv_latest: bool | None = None
    last_filing_year_min: int | None = None
    founded_year_min: int | None = None  # Gründungsjahr ≥ (young-company / ABM discovery)
    founded_year_max: int | None = None  # Gründungsjahr ≤
    gf_age_min: int | None = None  # primary Geschäftsführer current age ≥ (succession screen)
    manager_name: str | None = None  # case-insensitive substring on the primary manager's name
    #   (officer names are public Firmenbuch data; served only when EXPOSE_PERSONAL_DATA is set)


class Sort(BaseModel):
    field: str
    descending: bool = True


class CompanyCard(BaseModel):
    """Compact search result."""

    fnr: str
    name: str
    legal_form: str | None = None
    bundesland: str | None = None
    size_gkl: str | None = None
    bilanzsumme_band: str | None = None  # human size band (size_gkl is the UGB *filing* class)
    bilanzsumme_latest: float | None = None
    equity_ratio_latest: float | None = None
    revenue_latest: float | None = None
    growth_profile: str | None = None
    has_guv_latest: bool = False
    manager_name: str | None = None  # primary manager; null unless EXPOSE_PERSONAL_DATA is set
    is_financial_institution: bool = False  # bank/insurer → UGB figures absent by design (P2.1)


class SearchResponse(BaseModel):
    schema_version: str = "1.0"
    data_version_max: int = 0
    total: int = 0
    page: int = 1
    page_size: int = 25
    results: list[CompanyCard] = Field(default_factory=list)
    provenance: PublicProvenance = Field(default_factory=PublicProvenance)


class ErrorBody(BaseModel):
    code: Literal["not_found", "unauthorized", "rate_limited", "bad_request", "internal"]
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
