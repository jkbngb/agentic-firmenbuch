"""Growth math for absolute series (Appendix C.3).

* annual YoY rates (null when the prior year is <= 0),
* N-year CAGR over the *actual* span to the closest available start year
  (null when start or end <= 0 — so negative-equity series yield no CAGR),
* average yearly growth, sample volatility, min/max year.
All values rounded to 4 decimals to match the prototype.
"""

from __future__ import annotations

import statistics
from itertools import pairwise

from fbl_core.models import MetricSeries

ROUND = 4


def cagr(start: float, end: float, n: int) -> float | None:
    """Compound annual growth rate; None when start<=0, end<=0 or n<=0."""
    if start <= 0 or end <= 0 or n <= 0:
        return None
    result: float = round((end / start) ** (1 / n) - 1, ROUND)
    return result


def annual_growth_rates(history: dict[int, float]) -> dict[int, float]:
    """Year-over-year rates for consecutive years where the prior value is > 0."""
    rates: dict[int, float] = {}
    years = sorted(history)
    for prev, cur in pairwise(years):
        if cur - prev != 1:
            continue  # only consecutive calendar years
        start = history[prev]
        if start > 0:
            rates[cur] = round((history[cur] - start) / start, ROUND)
    return rates


def _closest_start_year(years: list[int], target: int) -> int | None:
    candidates = [y for y in years if y <= target]
    return max(candidates) if candidates else None


def compute_growth(series: MetricSeries, horizons: list[int]) -> MetricSeries:
    """Return a copy of *series* with growth fields populated (absolutes)."""
    history = {int(k): v for k, v in series.history.items()}
    if not history:
        return series.model_copy()
    years = sorted(history)
    latest_year = years[-1]
    latest = history[latest_year]

    rates = annual_growth_rates(history)
    out = series.model_copy(deep=True)
    out.latest = latest
    out.latest_year = latest_year
    out.annual_growth_rates = rates

    # growth_1y = the most recent consecutive YoY rate; if the immediately-preceding
    # year is missing, fall back to the closest available prior year (parity with the
    # CAGRs, Appendix C.3). Null when that start value is <= 0.
    out.growth_1y = rates.get(latest_year)
    if out.growth_1y is None:
        start_year = _closest_start_year(years, latest_year - 1)
        if start_year is not None and start_year != latest_year and history[start_year] > 0:
            out.growth_1y = round((latest - history[start_year]) / history[start_year], ROUND)

    for h in horizons:
        start_year = _closest_start_year(years, latest_year - h)
        value = None
        if start_year is not None and start_year != latest_year:
            value = cagr(history[start_year], latest, latest_year - start_year)
        if value is not None:
            out.growth_cagr[h] = value
        # Mirror the common horizons onto the named convenience fields.
        if h == 3:
            out.growth_3y_cagr = value
        elif h == 5:
            out.growth_5y_cagr = value

    if rates:
        out.growth_avg_yearly = round(statistics.fmean(rates.values()), ROUND)
        out.growth_min_year = min(rates.values())
        out.growth_max_year = max(rates.values())
        if len(rates) >= 2:
            out.growth_volatility = round(statistics.stdev(rates.values()), ROUND)
    return out
