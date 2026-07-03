"""fbl_derive — Stage 6b: ratios, growth, trends, size, peer percentiles (30_derived)."""

from __future__ import annotations

from .cohort import CohortStats, build_cohort_stats, company_gkl
from .derive import DEFAULT_HORIZONS, METRICS_VERSION, PRODUCER, derive
from .growth import annual_growth_rates, cagr, compute_growth
from .ratios import ratio_series

LAYER = "30_derived"

__all__ = [
    "DEFAULT_HORIZONS",
    "LAYER",
    "METRICS_VERSION",
    "PRODUCER",
    "CohortStats",
    "annual_growth_rates",
    "build_cohort_stats",
    "cagr",
    "company_gkl",
    "compute_growth",
    "derive",
    "ratio_series",
]
