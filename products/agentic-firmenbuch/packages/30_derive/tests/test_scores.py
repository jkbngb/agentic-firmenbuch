"""T11 — the three intent-score formulas, including the honest absent-input cases."""

from __future__ import annotations

from fbl_derive.scores import compute_scores


def test_growth_from_cagr_percentiles_is_their_mean() -> None:
    s = compute_scores(
        {"bilanzsumme_5y_cagr": 80.0, "eigenkapital_5y_cagr": 60.0, "equity_ratio": 55.0},
        growth_profile="stable",
        scale_percentile=91.0,
    )
    assert s is not None
    assert s["growth"] == 70.0  # mean(80, 60), NOT the profile fallback
    assert s["solidity"] == 55.0
    assert s["scale"] == 91.0
    assert set(s["basis"]) == {
        "bilanzsumme_5y_cagr",
        "eigenkapital_5y_cagr",
        "equity_ratio",
        "bilanzsumme",
    }


def test_growth_falls_back_to_profile_when_no_cagr() -> None:
    s = compute_scores({}, growth_profile="fast_growing", scale_percentile=None)
    assert s is not None
    assert s["growth"] == 85.0 and s["basis"] == ["growth_profile"]
    assert "solidity" not in s and "scale" not in s


def test_absent_when_no_inputs() -> None:
    assert compute_scores({}, growth_profile=None, scale_percentile=None) is None
    # An unknown profile is not a valid fallback either.
    assert compute_scores({}, growth_profile="mystery", scale_percentile=None) is None


def test_solidity_and_scale_independent_of_growth() -> None:
    s = compute_scores({"equity_ratio": 42.0}, growth_profile=None, scale_percentile=12.0)
    assert s == {"solidity": 42.0, "scale": 12.0, "basis": ["equity_ratio", "bilanzsumme"]}
