# `orchestration` (`fbl_orchestration`) — Stage 8 · the Job entrypoint

**Purpose:** the single Container Apps Job entrypoint that runs the pipeline. One image,
selected by `--mode`; holds the **singleton run lock**; runs the stages **sequentially**
per company over the changed set (§8.8, §15a).

```
fbl-pipeline --mode {sync-registry | backfill-ingest | ingest-fi | backfill-process | directories | daily}
```

## Modes
- **`sync-registry`** — seed/reconcile `99_registry` (first run = seed).
- **`backfill-ingest`** — download all raw → `90-raw` for the whole registry (**XML only**,
  `include_pdf=False` — the PDF siblings are skipped to spare storage across all 340k).
- **`ingest-fi`** — FI-targeted PDF ingest (ROADMAP P2.2): pull the official **PDF** abschlüsse
  for the regulated FIs so `get_document` can hand out a signed link. The worklist is the
  **union** of the OeNB register (`00_directories`) and the name heuristic (issue #15), so a
  register bank the heuristic missed is still drained. `include_pdf=True`, own checkpoint.
- **`directories`** — monthly: download the OeNB MFI/NMFI registers, archive verbatim+dated to
  `90-raw/_directories/`, and full-reconcile `00_directories` — the authoritative
  register-based `is_financial_institution` flag (issue #15). Touches only Blob + Cosmos + the
  public OeNB CSVs, so it needs no API key and no run lock.
- **`backfill-process`** — `consolidate → derive → present` over the whole registry.
- **`daily`** — detect changes since the watermark → ingest the new raw → process the
  dirty set → advance the watermark **only on full success**. Status-change-only dirty
  companies take the cheap **re-present** path (no re-derive). The delta also **derives register
  events** (name/seat/legal-form/management/capital changes) by diffing each company's master
  against the prior snapshot (issue #16, history from 2026-07-01).

## How it works
- **Run lock** (`runlock.py`): a lease doc in `99_registry`; a second invocation finds
  it held and exits 0 — never two runs at once (§15a.3).
- **`process_set`** (`pipeline.py`): consolidates the set, builds `CohortStats` **once
  per run over the whole consolidated universe**, then derives + presents each. One bad
  company dead-letters without failing the run.
- **`loaders.py`**: re-derives inputs from storage — `parse_all` re-parses `90-raw`
  (XML preferred, PDF-only → stub), `load_master` reads the archived `auszug`,
  `load_prev` reads the prior consolidated doc for supersedes/data_version chaining.
- Dependencies are injected (`PipelineContext`): Azure Blob/Cosmos + the HVD client in
  production, in-memory fakes in tests. **Idempotency:** `data_version` bumps only when
  content actually changes, so re-runs are true no-ops.

## Daily-delta operational notes (learned against prod volume — don't relearn)
- **Change feed is queried per Rechtsform.** `veraenderungen_firma` with an empty `RECHTSFORM`
  never returns (it streams the whole register at once — the same limit `sucheFirma` has), so
  `detect_changes` loops the 12 `DEFAULT_RECHTSFORMEN`. `veraenderungen_urkunden` (new filings)
  is form-agnostic and stays one call. **Coverage:** every company that files is caught via the
  urkunden feed regardless of form; the quarterly grind re-enumerates the namespace as backstop.
- **Detection heartbeats the lock + logs progress** (a day's feed can be thousands of changes);
  otherwise a long detect phase outlives the 30-min lease. Logs use `fbl_core.logging.get_logger`
  (a bare `logging.getLogger` has no handler here and prints nothing).
- **Run-lock ghost lease:** `az containerapp job stop` does NOT release `__runlock__`; the lease
  lingers ~30 min and the next run silently no-ops (`if not acquired: return 0`) — a fast
  "Succeeded" that did nothing. When supervising, delete the `__runlock__` doc in `99_registry`
  before re-running, or wait for expiry.
- **`DELTA_LOOKBACK_DAYS`** (default 3) floors the window N days back for overlap; set high once
  for a post-backfill catch-up.

## Run it standalone
```bash
uv run pytest packages/orchestration      # end-to-end on in-memory stores + fake source
```
Verified on **real data**: a one-FNR Initial Load (ingest → process) produced a complete
`10_presentation` doc (financials, ratios, size band + percentiles, gated management).

## Definition of Done (§8.8) — met
End-to-end run produces a valid `10_presentation` doc; re-run with no new data is a no-op
(hashes unchanged); a second concurrent invocation exits via the lock without work;
daily picks up a new filing and advances the watermark; status-change does a cheap
re-present. `ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
Wires [`ingest`](../90_ingest/README.md) → [`parse`](../70_parse/README.md) →
[`consolidate`](../50_consolidate/README.md) → [`derive`](../30_derive/README.md) →
[`present`](../10_present/README.md). Served by [`mcp_server`](../mcp_server/README.md) (Stage 9).

---
↑ [Repo root](../../../../README.md) · Specs: [Technische §8.8 / §15a](../../../../docs/specs/Technische_Spezifikation.md)
