# `agentic-firmenbuch` — Austria product

The live product over the Austrian **Firmenbuch** HVD. It owns every source-specific stage of
the pipeline; it consumes the shared, source-agnostic packages
([`fbl_core`](../../packages/core/README.md), [`fbl_auth`](../../packages/auth/README.md)) from
the monorepo root. Public pitch, quickstart, MCP-tool list and licence are in the
**[repo root README](../../README.md)**.

## Package layout

All stage packages live under `products/agentic-firmenbuch/packages/`. The stage directories
carry their layer-number prefix (Technische Spezifikation §3.4); the importable package keeps its
`fbl_*` name and exposes a `LAYER` constant. Stages never import each other — they communicate
through Blob/Cosmos; `orchestration` is the only module that wires them together.

| Directory | Package | Layer | Reads → Writes |
|---|---|---|---|
| [`99_registry`](packages/99_registry/README.md) | `fbl_registry` | `99_registry` | catalog of every Rechtsträger + watermark |
| [`90_ingest`](packages/90_ingest/README.md) | `fbl_ingest` | `90_raw` | HVD API → Blob `90-raw` (+ OeNB directories) |
| [`70_parse`](packages/70_parse/README.md) | `fbl_parse` | `70_parsed` | raw XML → `ParsedFiling` |
| [`50_consolidate`](packages/50_consolidate/README.md) | `fbl_consolidate` | `50_consolidated` | filings + master → one company doc |
| [`30_derive`](packages/30_derive/README.md) | `fbl_derive` | `30_derived` | ratios, growth, size class |
| [`10_present`](packages/10_present/README.md) | `fbl_present` | `10_presentation` | the GDPR-gated served document |

### Cross-cutting (un-numbered)

| Directory | Package | Role |
|---|---|---|
| [`core_at`](packages/core_at/README.md) | `fbl_core_at` | UGB position taxonomy (`mapping`), Firmenbuch domain models (`filing`/`company`/`mcp`), ÖNACE classification, OeNB/EIOPA directories, austria/formats/esvg helpers |
| [`firmenbuch_client`](packages/firmenbuch_client/README.md) | `fbl_firmenbuch_client` | the HVD (JustizOnline) SOAP/REST client |
| [`orchestration`](packages/orchestration/README.md) | `fbl_orchestration` | the `--mode` Job entrypoint that wires the stages |
| [`mcp_server`](packages/mcp_server/README.md) | `fbl_mcp_server` | the multi-tenant MCP tools + OAuth + playground |

> `core_at`, `30_derive` and `mcp_server` are AT-bound today. `core_at` is inherently
> source-specific; `derive`/`mcp_server` are *promotion-candidates* (source-agnostic algorithm,
> AT-shaped models) that move to `packages/` in a later model-abstraction pass. See the reuse
> table (Appendix R) in the Technische Spezifikation.

## Run its tests

```bash
uv run pytest products/agentic-firmenbuch          # all AT unit/fixture/integration tests
```

---
↑ [Repo root (agentic-first)](../../README.md) · Shared: [`core`](../../packages/core/README.md) · [`auth`](../../packages/auth/README.md)
