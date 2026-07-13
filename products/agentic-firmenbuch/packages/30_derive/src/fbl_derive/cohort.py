"""Size-band-relative peer percentiles (Appendix C.3).

Computed in a second pass over the universe (once per run): rank each company's
metric within its own ``gkl`` band, for bilanzsumme, equity_ratio, and the 5y CAGRs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from fbl_core_at.models import ConsolidatedCompany

from .growth import compute_growth

PERCENTILE_METRICS = (
    "bilanzsumme",
    "equity_ratio",
    "bilanzsumme_5y_cagr",
    "eigenkapital_5y_cagr",
)


def company_gkl(company: ConsolidatedCompany) -> str | None:
    """The company's size class: its own size.gkl, else the latest filing's gkl."""
    if company.size.gkl is not None:
        return company.size.gkl
    for ref in company.filings:  # filings are newest-first
        if ref.gkl:
            return ref.gkl
    return None


def _metric_values(company: ConsolidatedCompany) -> dict[str, float]:
    out: dict[str, float] = {}
    bilanz = company.financials.bilanz
    bs = bilanz.get("bilanzsumme")
    ek = bilanz.get("eigenkapital")
    if bs is not None and bs.latest is not None:
        out["bilanzsumme"] = bs.latest
        out["bilanzsumme_5y_cagr"] = compute_growth(bs, [5]).growth_5y_cagr  # type: ignore[assignment]
    if ek is not None and ek.latest is not None:
        out["eigenkapital_5y_cagr"] = compute_growth(ek, [5]).growth_5y_cagr  # type: ignore[assignment]
    if bs is not None and bs.latest and ek is not None and ek.latest is not None and bs.latest > 0:
        out["equity_ratio"] = ek.latest / bs.latest
    return {k: v for k, v in out.items() if v is not None}


@dataclass
class CohortStats:
    """Sorted metric values per gkl band, for percentile ranking."""

    by_gkl: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    def percentile(self, gkl: str | None, metric: str, value: float | None) -> float | None:
        if gkl is None or value is None:
            return None
        values = self.by_gkl.get(gkl, {}).get(metric)
        if not values:
            return None
        n = len(values)
        below = sum(1 for v in values if v < value)
        equal = sum(1 for v in values if v == value)
        return round(100.0 * (below + 0.5 * equal) / n, 1)  # mean-rank percentile


def build_cohort_stats(companies: Iterable[ConsolidatedCompany]) -> CohortStats:
    """Build the per-band metric distributions from all consolidated companies.

    Accepts any iterable (consumed lazily) so a bulk backfill can stream the consolidated
    universe one doc at a time — only the accumulated metric floats stay in memory, never
    the full set of company objects (§15a.1 memory safety)."""
    by_gkl: dict[str, dict[str, list[float]]] = {}
    for company in companies:
        gkl = company_gkl(company)
        if gkl is None:
            continue
        band = by_gkl.setdefault(gkl, {m: [] for m in PERCENTILE_METRICS})
        for metric, value in _metric_values(company).items():
            band[metric].append(value)
    for band in by_gkl.values():
        for values in band.values():
            values.sort()
    return CohortStats(by_gkl=by_gkl)
