# agentic-firmenbuch

> **Austria's entire company register, queryable by AI agents in plain language.** Official master data, annual accounts and key ratios for every firm – served over MCP, answered on real numbers instead of hallucinations.

**[Try the playground](https://www.agentic-firmenbuch.at/playground.html)** &nbsp;·&nbsp; **[Get a free key](https://www.agentic-firmenbuch.at)** &nbsp;·&nbsp; **[Quickstart ↓](#quickstart)**

A live, automated data product over the Austrian **Firmenbuch** (free EU **HVD** / High Value Datasets), served through a **multi-tenant MCP server**. A deterministic Azure pipeline ingests every Jahresabschluss (annual financial statement) for the whole company universe (~200k+), consolidates per company, computes ratios/growth/trends, and exposes it to MCP clients. **Version 1** = facts + clean derivations only (no scoring, no third-party enrichment, no NACE, no AI summaries).

## Quickstart

**Use the hosted service** – query official Firmenbuch data from an MCP client that accepts an HTTP header key (Claude Code, VS Code with GitHub Copilot, Cursor, …):

1. Get a free API key at **[agentic-firmenbuch.at](https://www.agentic-firmenbuch.at)** – just a verified email.
2. Add the server. **Claude Code** (terminal), one line:
   ```bash
   claude mcp add --scope user --transport http agentic-firmenbuch https://mcp.agentic-firmenbuch.at/mcp --header "X-API-Key: <your-key>"
   ```
   **GitHub Copilot / VS Code**: `code --add-mcp "{\"name\":\"agentic-firmenbuch\",\"type\":\"http\",\"url\":\"https://mcp.agentic-firmenbuch.at/mcp\",\"headers\":{\"X-API-Key\":\"<your-key>\"}}"`. Any other HTTP-MCP-Header client: URL `https://mcp.agentic-firmenbuch.at/mcp`, header `X-API-Key: <your-key>`.
3. Ask in natural language, e.g. *"Aktive GmbHs in Oberösterreich mit Bilanzsumme über 5 Mio. €, sortiert nach Umsatz."* The agent calls `search_companies` / `get_company_details` and answers with official data – no SDK required.

> **Claude Cowork & claude.ai** (sandboxed clients) don't take the API-key header – they connect via `Settings → Connectors → Add custom connector` with the URL `https://mcp.agentic-firmenbuch.at/mcp` and a one-time email login (OAuth, no key). Step-by-step with screenshots: **[agentic-firmenbuch.at/cowork.html](https://www.agentic-firmenbuch.at/cowork.html)**.

Prefer to try before signing up? Use the **[playground](https://www.agentic-firmenbuch.at/playground.html)**.

**Or run the pipeline yourself** – clone, `uv sync`, `uv run pytest` (offline, no Azure). See [Develop](#develop).

## Available MCP tools
| Tool | Purpose |
|---|---|
| `search_companies` | Filter / rank Austrian companies by region, size, balance-sheet total, equity ratio, revenue, growth profile, management age, last filing year, status. Returns a compact result card per match. |
| `get_company_details` | Full served profile of one company: identity, location, founding/filing years, size class, multi-year balance sheet + P&L, 13 computed ratios, growth, management, list of filings. |
| `get_full_record` | Superset of `get_company_details`: full 317-position UGB taxonomy, passthrough unknown codes, completeness, GuV-years, signatories history, derivations registry. |
| `get_company_history` | Filing-by-filing time series of every reported position for one company. |
| `find_peers` | K-nearest peer set for a company within its size class / region. |
| `get_cohort_summary` | Aggregate statistics (counts, percentiles, distributions) for a filtered cohort. |
| `get_coverage` | Per-Bundesland / per-Rechtsform / per-size-class coverage statistics for the served dataset. |
| `list_sectors` | Available legal-form (`Rechtsform`) and Bundesland codes for use in filters. |
| `describe_fields` | Self-describing field dictionary with type + null-rules + EBIT/EBITDA definition. |
| `get_document` | Fetch the URL/blob key for an original filed annual statement (XML or PDF). |

All tools return data **straight from the official Austrian Firmenbuch** (BMJ – Justiz, CC BY 4.0). No web scraping, no LLM-generated summaries, no third-party enrichment in V1. Every response carries `provenance.data_version` + `built_at` so the agent can cite the source.

**Currently served**: ~341,000 active legal entities across all Rechtsformen (GmbH, AG, KG, OG, EU, Genossenschaft, Privatstiftung, SE …). The full register has ~640,000 entities; the gap is companies without a published Jahresabschluss plus inactive/deleted entries, which are added step by step.

## Documentation
| Doc | What it is |
|---|---|
| [docs/Technische_Spezifikation_v1.md](docs/Technische_Spezifikation_v1.md) | The HOW – architecture, modules, schemas, runbook, edge cases, build order. **Primary.** |
| [docs/Fachliche_Spezifikation_v1.md](docs/Fachliche_Spezifikation_v1.md) | The WHAT/WHY – scope and business rules. |
| [docs/pipeline-step-samples.md](docs/pipeline-step-samples.md) | File format + golden sample for every pipeline stage. |
| [docs/FIELD_REFERENCE.md](docs/FIELD_REFERENCE.md) | **Served field dictionary** – every field each MCP tool returns, with type + null rules. Public page: [felder.html](https://www.agentic-firmenbuch.at/felder.html). |
| [docs/appendix_position_mapping.json](docs/appendix_position_mapping.json) | Full 317-entry canonical position taxonomy → copy to `core/mapping/`. |
| [docs/reference/](docs/reference/) | Official source material (API reference, JAb 4.0 XSDs/Excel). |

## Pipeline (numbered layers, `90 → 10`)
```
99_registry (foundation: all companies)  →  90_raw (Blob)  →  70_parsed (Blob)  →  50_consolidated  →  30_derived  →  10_presentation  →  MCP
                                                                        (Cosmos)            (Cosmos)        (Cosmos)
   side: 00_accounts (MCP signup)            reserved for v2: 40_enriched, 20_scored
```
`90_raw` is the **immutable source of truth** (every downloaded XML/PDF, kept forever). `70_parsed`
is a **write-through cache** of the per-filing `ParsedFiling` JSON – always re-derivable from raw,
so safe to drop/rebuild; it exists so a reprocess (re-consolidate/derive after a logic change)
**skips re-parsing** all filings, and so the lineage `inputs[]` in each consolidated doc resolve to a
real parsed document. `50/30/10` are the queryable Cosmos layers; `10_presentation` is what the MCP
serves.

## LAYER_MAP – which code owns which layer
Each pipeline-stage package directory is **prefixed with its layer number** so the
owner of every data layer is obvious. (Python module names can't start with a digit, so
the importable package keeps its `fbl_*` name; the number is also exposed as a `LAYER`
constant in each stage package.)

| Layer | Package (dir) | import | Store / container | Pydantic model | Sample |
|---|---|---|---|---|---|
| `99_registry` | [`99_registry`](packages/99_registry/README.md) | `fbl_registry` | Cosmos `99_registry` | `RegistryDoc` | §15a.0 doc |
| `90_raw` | [`90_ingest`](packages/90_ingest/README.md) | `fbl_ingest` | Blob `90-raw` | raw `Meta` + manifest | [Stage 0](docs/pipeline-step-samples.md) |
| `70_parsed` | [`70_parse`](packages/70_parse/README.md) | `fbl_parse` | Blob `70-parsed` | `ParsedFiling` | [Stage 1](docs/pipeline-step-samples.md) |
| `50_consolidated` | [`50_consolidate`](packages/50_consolidate/README.md) | `fbl_consolidate` | Cosmos `50_consolidated` | `ConsolidatedCompany` | [Stage 2](docs/pipeline-step-samples.md) |
| `30_derived` | [`30_derive`](packages/30_derive/README.md) | `fbl_derive` | Cosmos `30_derived` | `DerivedCompany` | [Stage 3](docs/pipeline-step-samples.md) |
| `10_presentation` | [`10_present`](packages/10_present/README.md) | `fbl_present` | Cosmos `10_presentation` | `PresentedCompany` | [Stage 4](docs/pipeline-step-samples.md) |

**Un-numbered** (not a single data layer): [`core`](packages/core/README.md) (`fbl_core`,
shared models/mappings/lineage/storage), [`firmenbuch_client`](packages/firmenbuch_client/README.md)
(`fbl_firmenbuch_client`, HVD SOAP adapter), [`orchestration`](packages/orchestration/README.md)
(`fbl_orchestration`, the `--mode` Job entrypoint), [`mcp_server`](packages/mcp_server/README.md)
(`fbl_mcp_server`, serving), [`auth`](packages/auth/README.md) (`fbl_auth`, `00_accounts`).
Plus [`infra/`](infra/README.md) (Bicep), [`tests/`](tests/README.md) (fixtures), `docs/`
(specs, incl. [API probe findings](docs/API_PROBE_FINDINGS.md)).

## Build status – Version 1 complete ✅
All ten §15 build stages are implemented, each committed in order, each with a passing
Definition of Done. **`ruff` + `mypy --strict` + `pytest` (with an 80% coverage gate) are
green** in CI. The HVD API was live-probed (§16 resolved) and the full chain
raw→present was verified on live data end-to-end.

- **Stage 10:** an auth-restricted **coverage** tool (XML vs PDF-only vs none, by
  format/status – §11) and **GitHub Actions CI** (`uv sync` → ruff → ruff format →
  `mypy --strict` → pytest with an 80% coverage gate, plus a Bicep-compile job).

**What's left to operate** (not code): provision Azure (`infra/setup.sh`, billable),
push the FIRMENBUCH_API_KEY to Key Vault, build/push images, then run the Initial Load
(`sync-registry → backfill-ingest → backfill-process`) and enable the daily cron.

## Develop
```bash
uv sync                       # create the workspace venv
uv run pytest                 # all fixture/unit/integration tests (offline)
uv run mypy packages          # strict types
uv run ruff check packages    # lint
```
**True end-to-end (live):** a separate, env-flag-guarded test runs a few real FNRs
through every layer (API → `90_raw` → … → `10_presentation` → MCP). Skipped by default.
```bash
FBL_E2E=1 uv run pytest tests/e2e -q     # needs FIRMENBUCH_API_KEY (env or .env)
```
See [`tests/e2e/`](tests/e2e/README.md). It uses in-memory stores + a tiny real pull –
**no Azure, no full backfill** (deployment is manual after review).

## License & data

Licensed under the **MIT License** (see [`LICENSE`](LICENSE)).

The data originates from the **Austrian Firmenbuch** (BMJ – Justiz), an EU High Value Dataset
licensed under **CC BY 4.0**. Any redistribution of the data must keep the attribution
*"Quelle: Österreichisches Firmenbuch / BMJ – Justiz (CC BY 4.0)"* (see [`NOTICE`](NOTICE)).

## Disclaimer – no warranty, use at your own risk

This software and any data it produces are provided **"AS IS", WITHOUT WARRANTY OF ANY KIND**,
express or implied (see the MIT License). The processed data is derived automatically from the
public Firmenbuch and is provided **without any guarantee of correctness, completeness, timeliness,
or fitness for a particular purpose**. It is **not** legal, tax, or financial advice and does **not**
replace an official Firmenbuch extract – the official register always prevails.

**Use of this software and the data is entirely at your own risk.** To the maximum extent permitted
by law, the authors and copyright holders accept **no liability** for any direct, indirect,
incidental, or consequential damages arising from its use. You are responsible for complying with
the CC BY 4.0 attribution requirement and all applicable data-protection, competition, and copyright
law when using or redistributing the data.
