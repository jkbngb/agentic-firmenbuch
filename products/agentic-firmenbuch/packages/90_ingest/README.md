# `90_ingest` (`fbl_ingest`) — Layer `90_raw`

**Layer:** `90_raw` | **reads:** HVD API (via `firmenbuch_client`) + `99_registry` | **writes:** Blob `90-raw` + `99_registry` state

**Purpose:** populate/reconcile the company catalog and fetch raw artifacts (§8.3, §15a).
The catalog itself (`Registry`, `RegistryDoc`, watermark) lives in the
[`99_registry`](../99_registry/README.md) package; this package *drives* it.

- **`sync_registry`** — idempotent upsert/diff vs the authoritative universe. First run
  on an empty registry = full **seed**; later runs = **reconcile** (refresh `last_seen`,
  mark vanished FNRs `deleted`). **Preferred seed = the data.gv.at HVD bulk dataset**
  (pass `bulk=BulkSource`) — the only true completeness guarantee. **Fallback = the
  hardened prefix-walk** (`enumerate.prefix_walk`): `EXAKTESUCHE=true`, `SUCHBEREICH=1`,
  all Rechtsformen; `MAX_PREFIX_DEPTH=20`; an **exhaustive split alphabet** (a–z, 0–9,
  `äöüß`, accented Latin, ` -.&:,'+/()`) **UNIONed with observed characters**;
  trailing-space guard; **checkpoint/resume** so a crashed/killed grind resumes from a
  persisted frontier (`BlobWalkCheckpoint` → `90-raw/_checkpoints/…`; a completed walk
  clears it so the next run re-walks fresh); and a **completeness self-check** that logs
  per-Rechtsform counts and **loudly flags** any depth-ceiling branch (never a silent
  keep-first-1000). The 1000 cap and bulk-file status are live-confirmed (§16,
  `docs/API_PROBE_FINDINGS.md`). Pass `report_blob=` to archive the **drift report**
  (`90-raw/_reports/sync-registry/{run_id}.json`) — the companies a reconcile had to add
  (`seeded_companies`) or delete (`deleted_companies`) = **what the daily change feed missed**.
- **`detect_changes`** — turns the (live-confirmed) change feeds into a dirty set:
  `Neueintragung` → new FNR, `Löschung` → status `deleted` + dirty (cheap re-present),
  `Änderung`/new docs → dirty. The active delta branch is `change_feed` (§16).
- **`run_ingest`** — for the dirty set: `sucheUrkunde` → download each new Jahresabschluss
  **XML and PDF** via `urkunde` → store immutably to `90-raw/{fnr}/{stichtag}/` with a
  per-Stichtag `_manifest.json`, and archive the master `auszug` (§5.1). Idempotent:
  a filing whose `doc_key` is already recorded is skipped. Per-company failures
  dead-letter; they never crash the batch.
- **`sync_directories`** (issue #15) — the register-based `is_financial_institution` source:
  download the OeNB MFI + NMFI lists (free CC-BY CSVs, Firmenbuchnummer-keyed), archive each
  verbatim + **dated** to `90-raw/_directories/{source}/{day}.csv` (lossless history), parse, and
  full-reconcile the `00_directories` Cosmos container — listed institutions → active, delisted →
  inactive (kept for history). No HVD API key needed. Run monthly (`--mode=directories`); served
  register-first by the MCP. Insurers (EIOPA + GLEIF FN bridge) are the planned addition (#17).
- **Lossless responses (§5.1)** — beyond the decoded documents, the verbatim API
  responses are archived byte-for-byte: `sucheUrkunde`/`auszug` under
  `90-raw/{fnr}/_responses/{run_id}/`, change feeds under `_changes/_responses/{run_id}/`.
  The capability is opt-in via the `RawCapturingSource` protocol (the live client
  implements it; test doubles don't, so archival is a transparent no-op offline).
  The `urkunde` envelope is the one response not duplicated — its payload is already
  preserved decoded.

## Dependencies
Depends on `fbl_core` (models, storage, lineage) and `fbl_firmenbuch_client`
(the `RegisterSource`). It does **not** import other pipeline stages — those
communicate only through Blob/Cosmos (§3). The store is injected as a
`BlobStoreLike`/`CosmosStoreLike`, so the same code runs on Azure or the in-memory fakes.

## Run it standalone
```bash
uv run pytest packages/90_ingest        # offline tests (fake RegisterSource + in-memory stores)
```
Verified on **real data** too: a one-FNR `run_ingest` against the live API stored
18 XML + 21 PDF filings, wrote manifests, archived the master extract, and the stored
raw XML re-parsed correctly (negative equity, aktiva == passiva).

## Definition of Done (§8.3) — met
Dry-run produces correct `90-raw` manifests + dirty set; watermark advances only on
success (enforced by the orchestrator, Stage 8); PDF stored alongside XML; idempotent
re-run downloads nothing new. `ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
**Previous:** [`firmenbuch_client`](../firmenbuch_client/README.md) (API) ·
**Next:** [`parse`](../70_parse/README.md) (`90-raw` → `70-parsed`), then
`consolidate`/`derive`. Orchestrated by [`orchestration`](../orchestration/README.md) (Stage 8).

---
↑ [Repo root](../../README.md) · Specs: [Technische §8.3/§15a](../../docs/specs/Technische_Spezifikation.md) · [API probe findings](../../docs/API_PROBE_FINDINGS.md)
