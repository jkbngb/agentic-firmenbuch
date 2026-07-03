"""Growth + ratio math vs the prototype's numbers (§8.6 DoD)."""

from __future__ import annotations

from typing import Any

import pytest

from fbl_core.models import MetricSeries
from fbl_derive import cagr, compute_growth, ratio_series


def _hist(block: dict[str, Any]) -> dict[int, float]:
    return {int(k): v for k, v in block["history"].items()}


def test_anlagedeckungsgrad_no_zero_division_on_negative_bilanzsumme() -> None:
    # Live-validation finding (548804s): a negative Bilanzsumme made the 5% floor negative,
    # admitting Anlagevermögen == 0 and dividing by zero. The ratio must skip such years.
    from fbl_derive.ratios import anlagedeckungsgrad_1

    bilanz = {
        "bilanzsumme": MetricSeries(history={2023: -1000.0, 2024: 2000.0}),
        "eigenkapital": MetricSeries(history={2023: -500.0, 2024: 800.0}),
        "anlagevermoegen": MetricSeries(history={2023: 0.0, 2024: 500.0}),
    }
    out = anlagedeckungsgrad_1(bilanz)  # must not raise
    assert 2023 not in out  # negative Bilanzsumme / zero Anlagevermögen year is skipped
    assert out[2024] == 800.0 / 500.0  # the healthy year still computes


@pytest.mark.parametrize("company", ["grama", "schubert"])
def test_growth_matches_prototype(company: str, request: pytest.FixtureRequest) -> None:
    data = request.getfixturevalue(company)
    block = data["financials"]["bilanz"]["bilanzsumme"]
    g = compute_growth(MetricSeries(history=_hist(block)), [1, 3, 5])
    for field in ("growth_1y", "growth_3y_cagr", "growth_5y_cagr", "growth_avg_yearly"):
        if block.get(field) is not None:
            assert getattr(g, field) == block[field], field


def test_equity_ratio_series_matches_prototype(grama: dict[str, Any]) -> None:
    rs = ratio_series(_hist(grama["ratios"]["equity_ratio"]))
    exp = grama["ratios"]["equity_ratio"]
    assert rs is not None
    for field in ("latest", "avg_3y", "avg_5y", "min_5y", "max_5y", "volatility", "trend"):
        assert getattr(rs, field) == exp[field], field


def test_declining_trend(schubert: dict[str, Any]) -> None:
    rs = ratio_series(_hist(schubert["ratios"]["equity_ratio"]))
    assert rs is not None and rs.trend == "declining"


def test_ratio_latest_null_when_latest_year_gated() -> None:
    # The latest fiscal year (2025) was gated/capped out of the ratio history; .latest
    # must be null, not the stale 2023 value (conformance §4.2).
    rs = ratio_series({2023: 7.1515}, latest_year=2025)
    assert rs is not None
    assert rs.latest is None
    assert rs.latest_year == 2025
    assert rs.trend is None
    assert rs.history == {2023: 7.1515}  # the older value is still visible in history


def test_ratio_latest_present_when_latest_year_has_value() -> None:
    rs = ratio_series({2024: 0.4, 2025: 0.5}, latest_year=2025)
    assert rs is not None and rs.latest == 0.5 and rs.latest_year == 2025


def test_cagr_null_on_nonpositive() -> None:
    assert cagr(-5.0, 10.0, 3) is None  # negative start
    assert cagr(10.0, -5.0, 3) is None  # negative end
    assert cagr(10.0, 20.0, 0) is None  # zero span
    assert cagr(100.0, 200.0, 1) == 1.0


def test_horizon_toggle_emits_extra_cagrs() -> None:
    # §8.6 DoD: toggling horizons to [1,2,3,4,5] emits the 2y/4y CAGRs with no other change.
    hist = {2020: 100.0, 2021: 110.0, 2022: 121.0, 2023: 133.1, 2024: 146.41, 2025: 161.05}
    base = compute_growth(MetricSeries(history=hist), [1, 3, 5])
    extended = compute_growth(MetricSeries(history=hist), [1, 2, 3, 4, 5])
    assert 2 in extended.growth_cagr and 4 in extended.growth_cagr
    assert 2 not in base.growth_cagr and 4 not in base.growth_cagr
    # named 3y/5y fields are unchanged by adding horizons
    assert extended.growth_3y_cagr == base.growth_3y_cagr
    assert extended.growth_5y_cagr == base.growth_5y_cagr


def test_closest_start_year_for_cagr() -> None:
    # Missing exact start year -> use the closest available and its actual span.
    hist = {2019: 100.0, 2024: 200.0}  # 5-year span, no intermediate years
    g = compute_growth(MetricSeries(history=hist), [3])
    # closest start <= 2024-3=2021 is 2019 -> span 5
    assert g.growth_cagr.get(3) == cagr(100.0, 200.0, 5)
