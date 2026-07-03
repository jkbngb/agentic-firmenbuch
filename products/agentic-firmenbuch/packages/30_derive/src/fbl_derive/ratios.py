"""Ratio math (Appendix C.2) with the prototype's meaningfulness caps + rolling stats.

A ratio is computed per year from the aligned Bilanz/GuV histories, then summarized
as a ``MetricSeries`` (latest, history, rolling avg/min/max over the last 5 years,
sample volatility, trend). Margins use ``umsatzerloese`` only, never ``rohergebnis``.
"""

from __future__ import annotations

import statistics

from fbl_core.models import MetricSeries
from fbl_core.models.metric import Trend

ROUND = 4
_TREND_BAND = 0.05  # relative deadband around avg_5y for "stable"


def _safe(
    numer: float | None, denom: float | None, *, require_positive: bool = False
) -> float | None:
    if numer is None or denom is None or denom == 0:
        return None
    if require_positive and denom <= 0:
        return None
    return numer / denom


def ratio_series(
    values_by_year: dict[int, float], *, latest_year: int | None = None
) -> MetricSeries | None:
    """Summarize a per-year ratio dict as a MetricSeries (rolling stats over last 5y).

    *latest_year* is the company's actual latest fiscal year. If that year's ratio was
    gated/capped out (absent from ``values_by_year``), ``latest`` is **null** rather than
    a stale older value — a consumer filtering on ``.latest`` must never get a
    multi-year-old number presented as current.
    """
    if not values_by_year:
        return None
    history = {y: round(v, ROUND) for y, v in values_by_year.items()}
    years = sorted(history)
    ref_year = latest_year if latest_year is not None else years[-1]
    latest_value = history.get(ref_year)  # None when the latest year is gated out
    last5 = [history[y] for y in years[-5:]]
    last3 = [history[y] for y in years[-3:]]
    avg_5y = round(statistics.fmean(last5), ROUND)
    return MetricSeries(
        latest=latest_value,
        latest_year=ref_year,
        history=history,
        avg_3y=round(statistics.fmean(last3), ROUND),
        avg_5y=avg_5y,
        min_5y=min(last5),
        max_5y=max(last5),
        volatility=round(statistics.stdev(last5), ROUND) if len(last5) >= 2 else None,
        trend=_trend(latest_value, avg_5y) if latest_value is not None else None,
    )


def _trend(latest: float, avg_5y: float) -> Trend:
    if avg_5y == 0:
        return "stable"
    rel = (latest - avg_5y) / abs(avg_5y)
    if rel > _TREND_BAND:
        return "improving"
    if rel < -_TREND_BAND:
        return "declining"
    return "stable"


def _hist(series: dict[str, MetricSeries], field: str) -> dict[int, float]:
    ms = series.get(field)
    return {int(k): v for k, v in ms.history.items()} if ms is not None else {}


def equity_ratio(bilanz: dict[str, MetricSeries]) -> dict[int, float]:
    aktiva, eigen = _hist(bilanz, "bilanzsumme"), _hist(bilanz, "eigenkapital")
    out = {}
    for y, a in aktiva.items():
        v = _safe(eigen.get(y), a, require_positive=True)
        if v is not None:
            out[y] = v
    return out


def debt_ratio(bilanz: dict[str, MetricSeries]) -> dict[int, float]:
    aktiva = _hist(bilanz, "bilanzsumme")
    rueck, verb = _hist(bilanz, "rueckstellungen"), _hist(bilanz, "verbindlichkeiten")
    out = {}
    for y, a in aktiva.items():
        if a > 0:
            out[y] = (rueck.get(y, 0.0) + verb.get(y, 0.0)) / a
    return out


def debt_to_equity(bilanz: dict[str, MetricSeries]) -> dict[int, float]:
    eigen, verb = _hist(bilanz, "eigenkapital"), _hist(bilanz, "verbindlichkeiten")
    out = {}
    for y, e in eigen.items():
        if e > 0 and y in verb:
            v = verb[y] / e
            if v <= 50:  # null if > 50 (noise)
                out[y] = v
    return out


def working_capital_ratio(bilanz: dict[str, MetricSeries]) -> dict[int, float]:
    umlauf, verb = _hist(bilanz, "umlaufvermoegen"), _hist(bilanz, "verbindlichkeiten")
    out = {}
    for y, vb in verb.items():
        if vb > 0 and y in umlauf:
            v = umlauf[y] / vb
            if v <= 20:
                out[y] = v
    return out


def anlagedeckungsgrad_1(bilanz: dict[str, MetricSeries]) -> dict[int, float]:
    aktiva, eigen = _hist(bilanz, "bilanzsumme"), _hist(bilanz, "eigenkapital")
    av = _hist(bilanz, "anlagevermoegen")
    out = {}
    for y, a in av.items():
        aktiva_y = aktiva.get(y)
        # aktiva_y > 0 is required: a negative Bilanzsumme makes the 5% floor negative, which
        # would admit a == 0 (Anlagevermögen) and divide by zero. With aktiva_y > 0 the floor
        # is positive, so a passing the 5% test is strictly positive.
        if aktiva_y and aktiva_y > 0 and a >= 0.05 * aktiva_y and y in eigen:
            v = eigen[y] / a
            if v <= 20:
                out[y] = v
    return out


def margin(guv: dict[str, MetricSeries], field: str, *, absolute: bool = False) -> dict[int, float]:
    umsatz = _hist(guv, "umsatzerloese")  # margins only on Umsatz, never Rohergebnis
    num = _hist(guv, field)
    out = {}
    for y, u in umsatz.items():
        if u > 0 and y in num:
            out[y] = (abs(num[y]) if absolute else num[y]) / u
    return out


def roa(bilanz: dict[str, MetricSeries], guv: dict[str, MetricSeries]) -> dict[int, float]:
    aktiva, jue = _hist(bilanz, "bilanzsumme"), _hist(guv, "jahresueberschuss")
    return {y: jue[y] / aktiva[y] for y in jue if aktiva.get(y, 0) > 0}


def roe(bilanz: dict[str, MetricSeries], guv: dict[str, MetricSeries]) -> dict[int, float]:
    eigen, jue = _hist(bilanz, "eigenkapital"), _hist(guv, "jahresueberschuss")
    return {y: jue[y] / eigen[y] for y in jue if eigen.get(y, 0) > 0}


def capital_profile(equity_ratio_latest: float | None) -> str | None:
    if equity_ratio_latest is None:
        return None
    if equity_ratio_latest < 0.15:
        return "over_leveraged"
    if equity_ratio_latest < 0.60:
        return "balanced"
    return "over_capitalized"
