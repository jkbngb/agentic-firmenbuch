"""MCP tool I/O contracts (Technische Spezifikation §9)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fbl_core.models.meta import Meta


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
    # Industry classification (v2, #34): written by the grind / the daily delta
    # (orchestration.industry_sync), carried as a first-class field so re-presents and
    # status-only refreshes never silently drop it.
    industry: dict[str, object] | None = None
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
    # Branch / industry (issue #19) — the fast peer-finder; exact match, most-specific wins.
    oenace_section: str | None = None  # ÖNACE 2025 section A–V (broad screen), e.g. "M"
    oenace_division: str | None = None  # 2-digit division, e.g. "68"
    oenace_group: str | None = None  # 3-digit group (precise peers), e.g. "68.3"
    geschaeftszweig: str | None = None  # case-insensitive substring on the Firmenbuch activity text
    # Location (issue #19) — filter directly instead of the get_company_details detour.
    postal_code: str | None = None  # PLZ prefix, e.g. "1010" (exact) or "10" (all 10xx)
    city: str | None = None  # case-insensitive substring on the seat city


class Sort(BaseModel):
    field: str
    descending: bool = True


class CompanyCard(BaseModel):
    """Compact search result."""

    fnr: str
    name: str
    legal_form: str | None = None
    bundesland: str | None = None  # Bundesland label (e.g. "Steiermark")
    postal_code: str | None = None  # PLZ of the seat — Ort/PLZ on the card, no detail call (#28)
    city: str | None = None  # seat city (e.g. "Graz")
    street: str | None = None  # seat street + number
    size_gkl: str | None = None
    bilanzsumme_band: str | None = None  # human size band (size_gkl is the UGB *filing* class)
    bilanzsumme_latest: float | None = None
    equity_ratio_latest: float | None = None
    revenue_latest: float | None = None
    growth_profile: str | None = None
    has_guv_latest: bool = False
    manager_name: str | None = None  # primary manager; null unless EXPOSE_PERSONAL_DATA is set
    is_financial_institution: bool = False  # bank/insurer → UGB figures absent by design (P2.1)
    geschaeftszweig: str | None = None  # Firmenbuch free-text activity (the industry source text)
    industry_section: str | None = None  # ÖNACE 2025 section (A–V) from the industry block (#34)
    oenace_division: str | None = None  # ÖNACE 2025 division (2-digit), e.g. "85" (#35)
    oenace_division_label: str | None = (
        None  # German division title, e.g. "Erziehung und Unterricht"
    )
    oenace_group: str | None = None  # ÖNACE 2025 group (3-digit), e.g. "85.5" (#35)
    oenace_group_label: str | None = None  # German group title, e.g. "Sonstiger Unterricht"
    # ÖNACE 2008 twin — the vintage the classifier predicted in (motor-vehicle trade is
    # division "45" here, split across 46/47 in 2025). Search filters match BOTH vintages, so
    # a card is self-explanatory whichever code a caller queried. Deterministic 2008-tree lookup.
    oenace_division_2008: str | None = None  # e.g. "45" (Kfz-Handel u. -Reparatur)
    oenace_division_2008_label: str | None = None  # German 2008 division title
    oenace_group_2008: str | None = None  # e.g. "45.1"
    oenace_group_2008_label: str | None = None  # German 2008 group title


class Relaxation(BaseModel):
    """One single-filter loosening the server found when a search returned zero hits (T6).

    ``dropped`` is the filter (or range unit) that, removed on its own, yields ``total`` matches
    — so the caller adjusts THAT filter instead of blindly retrying combinations. ``suggestion``
    carries the nearest achievable bound for a numeric range."""

    dropped: str  # e.g. "postal_code" or "bilanzsumme_range"
    total: int  # matches if this one filter is removed (always > 0)
    suggestion: str | None = None  # e.g. "nearest achievable bilanzsumme range: 0.8M–4.2M"


class SearchResponse(BaseModel):
    schema_version: str = "1.0"
    data_version_max: int = 0
    total: int = 0
    page: int = 1
    page_size: int = 25
    results: list[CompanyCard] = Field(default_factory=list)
    # True when more pages exist after this one (start + len(results) < total). Lets a client
    # page without re-deriving the arithmetic. Response-only, safe to ship (T4).
    has_more: bool = False
    # Present ONLY when total == 0 and ≥2 filters were active: which single filter to drop/loosen
    # to get hits, most-permissive first. Response-only (T6).
    relaxations: list[Relaxation] | None = None
    # The filters as ACTUALLY applied after normalization ("Wien"→"W", GmbH→"GE*" prefix, clamped
    # page_size) — so the caller instantly sees a mis-parsed input instead of silently getting the
    # wrong result set. Present when any filter was active. Response-only (T9).
    applied_filters: dict[str, Any] | None = None
    provenance: PublicProvenance = Field(default_factory=PublicProvenance)


class ErrorBody(BaseModel):
    code: Literal["not_found", "unauthorized", "rate_limited", "bad_request", "internal"]
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
