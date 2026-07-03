# `core` (`fbl_core`) — shared, source-agnostic foundation

**Purpose:** the source-agnostic library every product depends on. It contains **no
business logic and no Firmenbuch/UGB knowledge** — only the contracts and primitives
that stages serialize through. The Austria-specific domain (UGB taxonomy, Firmenbuch
models, ÖNACE) lives in [`fbl_core_at`](../../products/agentic-firmenbuch/packages/core_at/README.md);
`fbl_core` never imports it back (reuse table: Technische Spezifikation Appendix R).

- **`models/`** — the source-agnostic Pydantic v2 contracts (§6, §7): `Meta`/`LineageRef`/`Stage`
  (the lineage block) and `MetricSeries`/`Trend` (the uniform time-series object). Product-specific
  models (`ParsedFiling`, `ConsolidatedCompany`, `CompanyCard`, …) live in `fbl_core_at.models`.
- **`lineage.py`** — `new_doc_id`, `content_hash`, `stamp`, `lineage_ref` (§7). The
  content hash covers the business payload only (the whole meta block is excluded)
  so **identical inputs ⇒ identical hash** — the basis of skip-unchanged idempotency.
- **`config.py`** — typed settings + feature flags from the environment (§10).
- **`logging.py`** — structured JSON logging (§11).
- **`storage/`** — `BlobStore` (raw/parsed) and `CosmosStore` (consolidated→presented) +
  in-memory fakes. The Azure SDKs are imported **lazily** inside methods so the offline
  stages run without Azure installed or configured (§3.2).

## Inputs → outputs
This package produces no pipeline artifacts. It is imported by every product;
**the dependency arrow only points product → shared** (§3.5).

## Run it standalone
```bash
uv run pytest packages/core            # unit tests
uv run mypy packages/core              # strict types
uv run ruff check packages/core        # lint
```

## Definition of Done (§8.1)
- Metric/meta contracts round-trip and coerce correctly — `tests/` (+ the product's
  `core_at/tests/test_models.py` exercises them through the domain models).
- `content_hash` is stable across runs for identical input — `tests/test_lineage.py`.
- Storage clients honour the Blob/Cosmos Protocols — `tests/test_storage.py`.

## Key files
| Path | What |
|---|---|
| `src/fbl_core/models/meta.py`, `metric.py` | §6/§7 source-agnostic contracts |
| `src/fbl_core/lineage.py` | hashing / provenance helpers (§7) |
| `src/fbl_core/config.py` | typed settings + feature flags |
| `src/fbl_core/storage/` | Blob + Cosmos clients + in-memory fakes |

## Place in the monorepo
Shared foundation for every product. The Austrian pipeline builds on it via
[`fbl_core_at`](../../products/agentic-firmenbuch/packages/core_at/README.md) and the stage
packages under [`products/agentic-firmenbuch/`](../../products/agentic-firmenbuch/README.md).

---
↑ [Repo root](../../README.md) · Shared: [`auth`](../auth/README.md) · Specs: [Technische](../../docs/specs/Technische_Spezifikation.md) · [Fachliche](../../docs/specs/Fachliche_Spezifikation.md)
