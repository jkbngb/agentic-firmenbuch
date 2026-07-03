# `consolidate` (`fbl_consolidate`) — Stage 6a · `70_parsed` → `50_consolidated`

**Layer:** `50_consolidated` | **reads:** Blob `70-parsed` + master | **writes:** Cosmos `50_consolidated`

**Purpose:** merge all of a company's `ParsedFiling`s + master data into one
`ConsolidatedCompany` with per-line `MetricSeries.history` (facts only — growth is
added in `derive`). §8.5.

## What it does
- **Dedupe** resubmissions (one filing per Stichtag, last submitted wins; §15b-11).
- **Histories** per Bilanz/GuV field keyed by fiscal year. Each `MetricSeries` carries the
  official `source_codes` (the UGB code/element it was parsed from), `paragraph_ref` (e.g.
  `§224 Abs 2`, from the appendix), and `source_codes_by_year` when the code differs across
  years (Part A §-traceability).
- **Strict no-loss superset (Part B):** `financials.positions` carries a year-history
  series for **every recognized canonical** (full 317-entry taxonomy, keyed by canonical) —
  the typed `bilanz`/`guv` maps above are an ergonomic view of it — and
  `financials.passthrough` carries every **unknown source code**'s history. Nothing the
  parser found is reduced on the way up (§5.1).
- **GuV rollups:** `has_guv`, `has_guv_latest`, `guv_years`, `revenue_basis`,
  `completeness` (item counts per year). Most companies are Bilanz-only (§15b-9).
- **Management** from the latest filing's signatories + master persons: `primary_gf`
  (birth year only), `n_signatories_latest`, `signatories_stable_years`.
- **Identity/location/company** from `MasterData` (the canonical form of `auszug`),
  with `stammkapital` falling back to the Bilanz when master is absent.
- **Register events (`events.py`, issue #16):** `events[]` is derived from the daily change-feed
  delta — `master_signature` snapshots the change-relevant master fields and
  `derive_register_events` diffs it against the baseline (`event_baseline`) stored on the prior
  doc, emitting typed events (name/seat/legal-form/management/capital). Only the daily delta
  passes `today=`; the bulk backfill derives nothing. History starts 2026-07-01.
- **Lineage:** `inputs` = every parsed filing + the master extract; on rebuild,
  `supersedes` → prior doc and `data_version` is bumped. Deterministic: identical
  inputs → identical `content_hash`.

## Run it standalone
```bash
uv run pytest packages/50_consolidate     # multi-year fixture (490875a, 7 years)
```

## Definition of Done (§8.5) — met
Multi-year fixture consolidates to correct histories; `supersedes` + `data_version`
correct on rebuild; GuV rollups correct (Bilanz-only → no GuV); signatory stability
computed. `ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
**Previous:** [`parse`](../70_parse/README.md) · **Next:** [`derive`](../30_derive/README.md).
Depends only on `fbl_core` (does not import sibling stages, §3).

---
↑ [Repo root](../../../../README.md) · Specs: [Technische §8.5](../../../../docs/specs/Technische_Spezifikation.md) · [Pipeline samples](../../../../docs/pipeline-step-samples.md)
