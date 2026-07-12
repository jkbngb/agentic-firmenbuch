"""Normalized 0–100 intent scores per company (T11).

Materialized at derive time from the ALREADY EXISTING peer percentiles + growth profile, so the
MCP server can sort/rank by an intent signal (growth / solidity / scale) over an indexed path
instead of re-deriving anything per query. Pure functions, unit-tested. Missing inputs → the
score is simply ABSENT — never fabricated — and ``basis`` records which inputs each score used.
"""

from __future__ import annotations

from typing import Any

# When no CAGR percentile exists, fall back to the coarse growth profile → a representative
# score. Deliberately conservative midpoints; only used when the finer percentile is missing.
_GROWTH_PROFILE_SCORE = {
    "fast_growing": 85.0,
    "growing": 65.0,
    "stable": 50.0,
    "shrinking": 20.0,
}


def compute_scores(
    peer_percentiles: dict[str, float],
    growth_profile: str | None,
    scale_percentile: float | None,
) -> dict[str, Any] | None:
    """Build ``{growth, solidity, scale, basis}`` from the inputs, omitting any score whose
    inputs are absent. Returns ``None`` when nothing could be scored.

    - growth: mean of the available bilanzsumme/eigenkapital 5y-CAGR percentiles; else the
      growth-profile fallback; else absent.
    - solidity: the equity-ratio percentile; absent if unknown.
    - scale: the whole-dataset Bilanzsumme percentile ("all" cohort); absent if unknown.
    """
    scores: dict[str, Any] = {}
    basis: list[str] = []

    cagr_inputs = [
        (m, peer_percentiles[m])
        for m in ("bilanzsumme_5y_cagr", "eigenkapital_5y_cagr")
        if m in peer_percentiles
    ]
    if cagr_inputs:
        scores["growth"] = round(sum(v for _, v in cagr_inputs) / len(cagr_inputs), 1)
        basis.extend(m for m, _ in cagr_inputs)
    elif growth_profile in _GROWTH_PROFILE_SCORE:
        scores["growth"] = _GROWTH_PROFILE_SCORE[growth_profile]
        basis.append("growth_profile")

    if "equity_ratio" in peer_percentiles:
        scores["solidity"] = peer_percentiles["equity_ratio"]
        basis.append("equity_ratio")

    if scale_percentile is not None:
        scores["scale"] = scale_percentile
        basis.append("bilanzsumme")

    if not scores:
        return None
    scores["basis"] = basis
    return scores
