"""``derive`` — add ratios, growth, trends, size band, peer percentiles (§8.6)."""

from __future__ import annotations

from typing import cast

from fbl_core.lineage import lineage_ref, new_doc_id, stamp
from fbl_core.models import Meta, MetricSeries
from fbl_core_at.models import (
    ConsolidatedCompany,
    Derivations,
    DerivedCompany,
    Financials,
    Growth,
    Ratios,
    Size,
)
from fbl_core_at.models.company import SizeClass

from . import ratios as ratio_calc
from .cohort import ALL_COHORT, CohortStats, company_gkl
from .growth import compute_growth
from .scores import compute_scores

_SIZE_CLASSES = ("W", "K", "M", "G")

PRODUCER = "derive@1.0.0"
METRICS_VERSION = "1.0"
DEFAULT_HORIZONS = [1, 3, 5]

_FORMULAS = {
    "ratios.equity_ratio": "eigenkapital / bilanzsumme",
    "ratios.debt_ratio": "(rueckstellungen + verbindlichkeiten) / bilanzsumme",
    "ratios.debt_to_equity": "verbindlichkeiten / eigenkapital",
    "ratios.working_capital_ratio": "umlaufvermoegen / verbindlichkeiten",
    "ratios.anlagedeckungsgrad_1": "eigenkapital / anlagevermoegen",
    "ratios.ebit_margin": "ebit / umsatzerloese",
    "ratios.ebit_strict_margin": "ebit_strict / umsatzerloese",
    "ratios.ebitda_margin": "ebitda / umsatzerloese",
    "ratios.net_margin": "jahresueberschuss / umsatzerloese",
    "ratios.roa": "jahresueberschuss / bilanzsumme",
    "ratios.roe": "jahresueberschuss / eigenkapital",
    "growth.profile": "rule(umsatz_3y_cagr | rohergebnis_3y_cagr | bilanzsumme_3y_cagr)",
    "size.peer_percentiles": "percentile_rank(metric, cohort=gkl)",
}


def derive(
    company: ConsolidatedCompany,
    *,
    growth_horizons: list[int] | None = None,
    cohort_stats: CohortStats | None = None,
    run_id: str = "adhoc",
) -> DerivedCompany:
    """Compute the derived layer for one company."""
    horizons = growth_horizons or DEFAULT_HORIZONS
    cohort = cohort_stats or CohortStats()

    financials = _grow_financials(company.financials, horizons)
    employees = compute_growth(company.employees, horizons) if company.employees else None
    ratios = _build_ratios(financials)
    size = _build_size(company, financials, cohort)
    growth = _build_growth(financials)

    # Intent scores (T11): scale ranks Bilanzsumme over the WHOLE dataset ("all" cohort), the
    # other two reuse the per-gkl peer percentiles just computed. Absent when inputs are missing.
    bs_series = financials.bilanz.get("bilanzsumme")
    bs_latest = bs_series.latest if bs_series else None
    scale_pct = cohort.percentile(ALL_COHORT, "bilanzsumme", bs_latest)
    scores = compute_scores(size.peer_percentiles, growth.profile, scale_pct)

    meta = _derived_meta(company, run_id)

    der = DerivedCompany(
        identity=company.identity,
        location=company.location,
        company=company.company,
        size=size,
        financials=financials,
        employees=employees,
        management=company.management,
        filings=company.filings,
        events=company.events,
        ratios=ratios,
        growth=growth,
        scores=scores,
        derivations=Derivations(metrics_version=METRICS_VERSION, formulas=dict(_FORMULAS)),
        meta=meta,
    )
    stamp(der.meta, der.model_dump(mode="json"), stage_time_key="derived_at")
    return der


def _grow_financials(financials: Financials, horizons: list[int]) -> Financials:
    out = financials.model_copy(deep=True)
    out.bilanz = {k: compute_growth(v, horizons) for k, v in financials.bilanz.items()}
    out.guv = {k: compute_growth(v, horizons) for k, v in financials.guv.items()}
    # Strict no-loss superset (Part B): grow the full-taxonomy positions + passthrough too,
    # so derived ⊇ consolidated (every line carried, never reduced).
    out.positions = {k: compute_growth(v, horizons) for k, v in financials.positions.items()}
    out.passthrough = {k: compute_growth(v, horizons) for k, v in financials.passthrough.items()}
    return out


def _build_ratios(financials: Financials) -> Ratios:
    bilanz, guv = financials.bilanz, financials.guv
    ref = financials.latest_year  # the company's actual latest fiscal year

    def rs(values: dict[int, float]) -> MetricSeries | None:
        return ratio_calc.ratio_series(values, latest_year=ref)

    equity = rs(ratio_calc.equity_ratio(bilanz))
    return Ratios(
        equity_ratio=equity,
        debt_ratio=rs(ratio_calc.debt_ratio(bilanz)),
        debt_to_equity=rs(ratio_calc.debt_to_equity(bilanz)),
        working_capital_ratio=rs(ratio_calc.working_capital_ratio(bilanz)),
        anlagedeckungsgrad_1=rs(ratio_calc.anlagedeckungsgrad_1(bilanz)),
        ebit_margin=rs(ratio_calc.margin(guv, "ebit")),
        ebit_strict_margin=rs(ratio_calc.margin(guv, "ebit_strict")),
        ebitda_margin=rs(ratio_calc.margin(guv, "ebitda")),
        net_margin=rs(ratio_calc.margin(guv, "jahresueberschuss")),
        personalkostenquote=rs(ratio_calc.margin(guv, "personalaufwand", absolute=True)),
        materialaufwandsquote=rs(ratio_calc.margin(guv, "materialaufwand", absolute=True)),
        roa=rs(ratio_calc.roa(bilanz, guv)),
        roe=rs(ratio_calc.roe(bilanz, guv)),
        capital_profile=ratio_calc.capital_profile(equity.latest if equity else None),
    )


def _effective_size(bilanzsumme: float | None) -> str | None:
    if bilanzsumme is None:
        return None
    if bilanzsumme >= 100_000_000:
        return "very_large"
    if bilanzsumme >= 25_000_000:
        return "large"
    if bilanzsumme >= 6_250_000:
        return "medium"
    if bilanzsumme >= 450_000:
        return "small"
    return "micro"


def _build_size(company: ConsolidatedCompany, financials: Financials, cohort: CohortStats) -> Size:
    gkl = company_gkl(company)
    bs = financials.bilanz.get("bilanzsumme")
    ek = financials.bilanz.get("eigenkapital")
    bilanzsumme_latest = bs.latest if bs else None
    equity_ratio_latest = (
        ek.latest / bs.latest
        if bs and ek and bs.latest and ek.latest is not None and bs.latest > 0
        else None
    )
    percentiles: dict[str, float] = {}
    for metric, value in (
        ("bilanzsumme", bilanzsumme_latest),
        ("equity_ratio", equity_ratio_latest),
        ("bilanzsumme_5y_cagr", bs.growth_5y_cagr if bs else None),
        ("eigenkapital_5y_cagr", ek.growth_5y_cagr if ek else None),
    ):
        p = cohort.percentile(gkl, metric, value)
        if p is not None:
            percentiles[metric] = p
    return Size(
        gkl=_as_size_class(gkl),
        bilanzsumme_band=_effective_size(bilanzsumme_latest),
        peer_percentiles=percentiles,
    )


def _as_size_class(gkl: str | None) -> SizeClass | None:
    """Coerce a raw size-class string to the W/K/M/G literal, else None."""
    return cast(SizeClass, gkl) if gkl in _SIZE_CLASSES else None


def _build_growth(financials: Financials) -> Growth:
    """Growth profile by priority: umsatz → rohergebnis → bilanzsumme 3y CAGR."""
    candidates = [
        ("umsatz", financials.guv.get("umsatzerloese")),
        ("rohergebnis", financials.guv.get("rohergebnis")),
        ("bilanzsumme", financials.bilanz.get("bilanzsumme")),
    ]
    for method, series in candidates:
        if series is not None and series.growth_3y_cagr is not None:
            return Growth(profile=_classify(series.growth_3y_cagr), method=method)
    return Growth(profile=None, method=None)


def _classify(cagr: float) -> str:
    if cagr < -0.05:
        return "shrinking"
    if cagr < 0.03:
        return "stable"
    if cagr < 0.15:
        return "growing"
    return "fast_growing"


def _derived_meta(company: ConsolidatedCompany, run_id: str) -> Meta:
    meta = Meta(
        doc_id=new_doc_id(),
        entity_id=company.identity.fnr,
        stage="derived",
        producer=PRODUCER,
        run_id=run_id,
        metrics_version=METRICS_VERSION,
        data_version=company.meta.data_version,
        lineage=[lineage_ref(company.meta)],
    )
    if company.meta.timestamps:
        meta.timestamps.update(company.meta.timestamps)
    return meta
