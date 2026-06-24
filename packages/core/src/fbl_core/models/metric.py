"""The uniform metric object used for every time series (Technische Spezifikation §6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Trend = Literal["improving", "stable", "declining"]


class MetricSeries(BaseModel):
    """A single canonical time series (an absolute line item or a ratio).

    Absolutes populate the growth fields (YoY + CAGR); ratios populate the
    rolling avg/min/max/trend fields. Fields not applicable to a kind stay None.
    """

    latest: float | None = None
    latest_year: int | None = None
    history: dict[int, float] = Field(default_factory=dict)  # {year: value}
    # Official UGB code(s) this line item was parsed from — HGB_*/XXX_* (legacy/fb2025)
    # or the JAb 4.0 element name (jab40_semantic). Empty for computed series (ratios).
    source_codes: list[str] = Field(default_factory=list)
    # Per-year codes, populated ONLY when they differ across years for this canonical.
    source_codes_by_year: dict[int, list[str]] = Field(default_factory=dict)
    # Human UGB §-reference derived from the code (e.g. "§224 Abs 2 A II"), §-traceability.
    paragraph_ref: str | None = None
    annual_growth_rates: dict[int, float] = Field(default_factory=dict)  # {year: yoy}
    growth_1y: float | None = None
    growth_3y_cagr: float | None = None
    growth_5y_cagr: float | None = None
    # Generic per-horizon CAGRs ({horizon: cagr}); lets growth_horizons add 2y/4y/10y
    # without a schema change (§8.6). The named 1y/3y/5y fields mirror the common ones.
    growth_cagr: dict[int, float] = Field(default_factory=dict)
    growth_avg_yearly: float | None = None
    growth_volatility: float | None = None
    growth_min_year: float | None = None
    growth_max_year: float | None = None
    # ratios additionally use these (absolutes leave them None):
    avg_3y: float | None = None
    avg_5y: float | None = None
    min_5y: float | None = None
    max_5y: float | None = None
    volatility: float | None = None  # rolling sample stdev of a ratio series
    trend: Trend | None = None
