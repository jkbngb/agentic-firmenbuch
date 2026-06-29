# `packages/` — the uv workspace

Every member of the `uv` workspace lives here, one directory per responsibility. Each package
has its own `README.md`; the **[repo root README](../README.md) is the master index** in pipeline
order (`90 → 10`) with the full `LAYER_MAP`. This file is just a flat directory guide.

## Pipeline-stage packages (layer-numbered)

The stage directories carry their layer-number prefix so it's obvious which code owns which
layer (Technische Spezifikation §3.4). The importable package keeps its `fbl_*` name and exposes
a `LAYER` constant.

| Directory | Package | Layer | Reads → Writes |
|---|---|---|---|
| [`99_registry`](99_registry/README.md) | `fbl_registry` | `99_registry` | catalog of every Rechtsträger + watermark |
| [`90_ingest`](90_ingest/README.md) | `fbl_ingest` | `90_raw` | HVD API → Blob `90-raw` (+ OeNB directories) |
| [`70_parse`](70_parse/README.md) | `fbl_parse` | `70_parsed` | raw XML → `ParsedFiling` |
| [`50_consolidate`](50_consolidate/README.md) | `fbl_consolidate` | `50_consolidated` | filings + master → one company doc |
| [`30_derive`](30_derive/README.md) | `fbl_derive` | `30_derived` | ratios, growth, size class |
| [`10_present`](10_present/README.md) | `fbl_present` | `10_presentation` | the GDPR-gated served document |

## Cross-cutting packages (un-numbered)

| Directory | Package | Role |
|---|---|---|
| [`core`](core/README.md) | `fbl_core` | models, mapping, lineage, config, storage clients, directories, ESVG |
| [`firmenbuch_client`](firmenbuch_client/README.md) | `fbl_firmenbuch_client` | the HVD (JustizOnline) SOAP/REST client |
| [`orchestration`](orchestration/README.md) | `fbl_orchestration` | the `--mode` Job entrypoint that wires the stages |
| [`mcp_server`](mcp_server/README.md) | `fbl_mcp_server` | the multi-tenant MCP tools + OAuth + playground |
| [`auth`](auth/README.md) | `fbl_auth` | signup, token hashing, metering, ACS email |

## Conventions

- Self-contained (§3.2): a package imports `fbl_core` and never a sibling **stage** package.
- `ruff` + `mypy --strict` + `pytest` must pass for the whole workspace (`uv run …`).
- Each README links **up** to the root and **across** to its neighbouring stages.

---
↑ [Repo root](../README.md) · Specs: [Technische Spezifikation](../docs/Technische_Spezifikation.md)
