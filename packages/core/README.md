# `core` (`fbl_core`) — shared foundation

**Purpose:** the shared library every pipeline stage depends on. It contains **no
business logic** (§8.1) — only the contracts and primitives that the stages
serialize through:

- **`models/`** — the canonical Pydantic v2 data contracts (§6): `Meta`/`LineageRef`
  (lineage), `MetricSeries` (the uniform time-series object), `ParsedFiling` +
  `Bilanz`/`GuV`/`Signatory` (the `70_parsed` shape), `ConsolidatedCompany` /
  `DerivedCompany` (the Cosmos layers), and the MCP I/O contracts (§9).
- **`mapping/`** — the canonical position taxonomy. `appendix_position_mapping.json`
  (317 entries) is **copied verbatim** from `docs/`; `canonical.py` indexes it into
  `HGB_*`/`v4_element` → canonical lookups and binds the `Bilanz`/`GuV` model fields
  to their canonical entries (Appendix C/D).
- **`lineage.py`** — `new_doc_id`, `content_hash`, `stamp`, `lineage_ref` (§7). The
  content hash covers the business payload only (the whole meta block is excluded)
  so **identical inputs ⇒ identical hash** — the basis of skip-unchanged idempotency.
- **`config.py`** — typed settings + feature flags from the environment (§10).
- **`logging.py`** — structured JSON logging (§11).
- **`storage/`** — `BlobStore` (raw/parsed) and `CosmosStore` (consolidated→presented).
  The Azure SDKs are imported **lazily** inside methods so the offline stages run
  without Azure installed or configured (§3.2).

## Inputs → outputs
This package produces no pipeline artifacts. It is imported by every other package;
**siblings never import each other — shared code lives here** (§3).

## Run it standalone
```bash
uv run pytest packages/core            # unit tests
uv run mypy packages/core              # strict types
uv run ruff check packages/core        # lint
```

## Definition of Done (§8.1)
- Models round-trip (serialize/parse) the golden fixtures — `tests/test_models.py`.
- `content_hash` is stable across runs for identical input — `tests/test_lineage.py`.
- Mappings cover every canonical Bilanz/GuV field for **both** formats —
  `tests/test_mapping.py`.

## Key files
| Path | What |
|---|---|
| `src/fbl_core/models/` | §6 data contracts |
| `src/fbl_core/mapping/appendix_position_mapping.json` | verbatim 317-entry taxonomy |
| `src/fbl_core/mapping/canonical.py` | taxonomy loader + field maps |
| `src/fbl_core/lineage.py` | hashing / provenance helpers (§7) |
| `src/fbl_core/storage/` | Blob + Cosmos clients |

## Place in the pipeline
Foundation for all stages. **Next stage:** [`parse`](../70_parse/README.md) (raw XML →
`ParsedFiling`).

---
↑ [Repo root](../../README.md) · Specs: [Technische](../../docs/Technische_Spezifikation.md) · [Fachliche](../../docs/Fachliche_Spezifikation.md) · [Pipeline samples](../../docs/pipeline-step-samples.md)
