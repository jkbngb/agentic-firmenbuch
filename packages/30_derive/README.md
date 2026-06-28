# `derive` (`fbl_derive`) — Stage 6b · `50_consolidated` → `30_derived`

**Layer:** `30_derived` | **reads:** Cosmos `50_consolidated` | **writes:** Cosmos `30_derived`

**Purpose:** add `ratios`, `growth`, trends, size band, peer percentiles, and the
`derivations` catalog (§8.6, Appendix C). Deterministic, no LLM.

## What it computes
- **Growth** (absolutes): YoY annual rates, N-year **CAGR over the actual span** to the
  closest available start year (null when start/end ≤ 0 — negative-equity series yield
  no CAGR), average yearly growth, sample volatility, min/max year. Horizons are config
  (`[1,3,5]` default); extra horizons populate `growth_cagr` without a schema change.
- **Ratios** (Appendix C.2) with the meaningfulness caps: equity_ratio, debt_ratio,
  debt_to_equity, working_capital_ratio, anlagedeckungsgrad_1, ebit/ebitda/net margins
  (on Umsatz only, never Rohergebnis), personalkostenquote, roa, roe — each a
  `MetricSeries` with rolling avg_3y/avg_5y/min_5y/max_5y, sample volatility, and trend
  (vs avg_5y with a 5% deadband). Plus `capital_profile`.
- **Size:** `gkl` (W/K/M/G from the filing), `band` (effective size by Bilanzsumme),
  and **peer_percentiles** ranked within the same `gkl` band (`cohort.py`, second pass
  over the universe).
- **Growth profile:** priority `umsatz → rohergebnis → bilanzsumme` 3y CAGR, classified
  shrinking/stable/growing/fast_growing.

**Strict no-loss superset (Part B):** derived is a superset of consolidated — it grows the
full-taxonomy `financials.positions` and `financials.passthrough` maps too (not just the
typed Bilanz/GuV), so no line item is reduced. `source_codes`/`paragraph_ref` ride through
the deep-copy unchanged (Part A §-traceability).

The math is **validated to the prototype's exact numbers** (the `grama`/`schubert`
consolidated examples) in the tests.

## Run it standalone
```bash
uv run pytest packages/30_derive     # growth/ratio math vs prototype numbers, cohort, profiles
```

## Definition of Done (§8.6) — met
Ratio/growth outputs match the prototype's numbers (equity_ratio series + bilanzsumme
growth to 4 decimals); toggling `growth_horizons` to `[1,2,3,4,5]` emits the 2y/4y
CAGRs with no other change. `ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
**Previous:** [`consolidate`](../50_consolidate/README.md) · **Next:** `present` (Stage 7).
The universe-wide `CohortStats` is built once per run (by the orchestrator) and passed in.

---
↑ [Repo root](../../README.md) · Specs: [Technische §8.6 / Appendix C](../../docs/Technische_Spezifikation.md)
