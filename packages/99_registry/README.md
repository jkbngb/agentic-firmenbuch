# `99_registry` (`fbl_registry`) — Layer `99_registry`

**Layer:** `99_registry` | **reads:** Cosmos `99_registry` | **writes:** Cosmos `99_registry`

**Purpose:** the authoritative **catalog of every company** and its processing state
(§15a.0). One document per FNR plus a watermark singleton. The registry is the source of
truth for *which companies exist and their state*; Blob `90-raw` is the source of truth
for *documents we have*. It precedes raw ingestion and drives every download, rebuild,
and reconciliation.

## What it holds
- **`RegistryDoc`** (per FNR): `name` (company name from the sweep/bulk), `rechtsform`
  (legal-form code, e.g. `GES`/`AKT`), `status`
  (active/historical/deleted), `source`, `known_filings` (+ hashes), `pipeline_state`
  (clean/dirty/failed), `dirty_reason`, `data_version`, `last_seen_in_registry`,
  `dead_letter`. Kept lean — a catalog + work-queue; financials live in 50/30/10.
- **`Watermark`** singleton (`__watermark__`): last processed change-feed date.
- **`Registry`** — catalog operations over a `CosmosStoreLike` (Azure or in-memory fake):
  `ensure`/`get`/`put`, `mark_dirty`/`mark_clean`/`dead_letter`, `record_filing`,
  `set_status`, `all_fnrs`/`dirty_fnrs`/`iter_docs`, watermark get/set. Reserved
  singletons (watermark, run lock) use `__`-prefixed ids and are excluded from company sets.

## Run it standalone
```bash
uv run pytest packages/99_registry
```

## Place in the pipeline
The foundation. Populated/reconciled by [`90_ingest`](../90_ingest/README.md)
(`sync_registry`) and updated daily by [`orchestration`](../orchestration/README.md).
Depends only on [`core`](../core/README.md).

---
↑ [Repo root](../../README.md) · Specs: [Technische §15a.0](../../docs/Technische_Spezifikation_v1.md)
