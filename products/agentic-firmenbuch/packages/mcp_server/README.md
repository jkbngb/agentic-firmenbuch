# `mcp_server` (`fbl_mcp_server`) — Stage 9 · serving `10_presentation`

**Purpose:** the FastMCP app exposing the Firmenbuch tools (§9) over `10_presentation`, with
auth + rate limiting enforced before any tool runs.

## Tools (§9)
| Tool | Returns |
|---|---|
| `describe_fields()` | **self-describing field catalog** — every field at each tier (card → profile → full record), code tables, and availability/null rules. Lets an agent discover the data shape without guessing from a card. Human page: [felder.html](https://www.agentic-firmenbuch.at/felder.html) / [docs/FIELD_REFERENCE.md](../../docs/FIELD_REFERENCE.md). |
| `search_companies(filters, sort, page, page_size)` | paginated `CompanyCard` results — a **compact summary card** (10 fields), NOT the full record. Filters incl. `name` (company-name substring), legal form, Bundesland, size class, financial ranges, growth, GuV flags, last-filing-year, `gf_age_min`. `sort.field` ∈ {bilanzsumme, revenue, equity_ratio, employees, last_filing_year}. Runs **server-side in Cosmos** (WHERE + ORDER BY + OFFSET/LIMIT); bundesland/legal_form full-name↔code mapping applied. |
| `get_company_details(fnr)` | full served profile (internal hash chain omitted); each line item carries its `source_codes` + `paragraph_ref` (Part A) |
| `get_company_history(fnr, metrics)` | per-metric time series, each with `source_codes`, `source_codes_by_year`, `ugb_paragraph` (Part A) |
| `get_full_record(fnr)` | the COMPLETE consolidated/derived record — full `positions`/`passthrough`/`completeness`, nothing reduced (Part B §5.1); officer names gated |
| `get_document(doc_key)` | a **time-limited signed download link** to a company's official Jahresabschluss artifact in `90-raw`. `doc_key` is a filing's `document_ref` (`{fnr}:{stichtag}`, stamped by `get_company_details`), a bare FNR (→ latest filing), or a legacy doc_key. The blob path is read from the per-Stichtag `_manifest.json`; the chosen artifact (the **PDF for a bank/insurer**) is signed with a short-lived **User-Delegation SAS** — the URL is returned, never the bytes. Emits the `financial_institution` flag + caveat for FIs, whose figures live only in the PDF (ROADMAP P2.2). `download` is `null` if nothing is ingested for that filing or no blob is configured. |
| `list_sectors()` | legal-form + size-class taxonomy with counts |
| `get_cohort_summary(dimension, value)` | aggregate for gkl / bundesland / legal_form |
| `find_peers(fnr, n)` | nearest companies by Bilanzsumme in the same size class |

Every response carries the §8.9 envelope (`schema_version`, `data_version`,
`results|result`, `provenance`). Errors are typed (`not_found | unauthorized |
rate_limited | bad_request | internal`).

**Three data tiers (deliberate, best practice).** `search_companies` returns a compact
summary card for ranking/scanning; `get_company_details` returns one company's full served
profile; `get_full_record` returns the superset (full 317-position taxonomy + lineage). An
agent escalates from card → profile → full record as needed. `describe_fields` documents
all three so the shape is discoverable, not guessed. Authoritative dictionary:
[docs/FIELD_REFERENCE.md](../../docs/FIELD_REFERENCE.md) · public page
[felder.html](https://www.agentic-firmenbuch.at/felder.html).

**Document downloads (§7.2, ROADMAP P2.2).** `get_document` mints a User-Delegation SAS via
`BlobStore.download_link`, so the managed identity needs the **`Storage Blob Delegator`** role
in addition to `Storage Blob Data Contributor` (see [`infra/modules/rbac.bicep`](../../infra/modules/rbac.bicep));
without it `get_user_delegation_key` 403s. The banks/insurers whose PDF abschlüsse this serves
are pulled into `90-raw` by the [`ingest-fi`](../orchestration/README.md) pipeline mode.

**Auth is header-based:** the API key is read from the `X-API-Key` request header (set once
at `claude mcp add … --header`), never passed as a tool argument — so it never appears in a
tool-call payload and the agent doesn't need to know it.

## Design
- **`service/`** — the pure read tools over a `CosmosStoreLike` (unit-tested against the
  in-memory store), split into cohesive submodules over a shared `_common` support layer:
  `search.py`, `records.py` (details/history/full_record/describe_fields), `documents.py`
  (+ SAS download), `cohort.py` (cohort + peers), `stats.py` (coverage + sector
  materialized view). `service/__init__.py` re-exports the tools, so `service.<tool>` and
  `from fbl_mcp_server.service import store_stats` are unchanged. Filters/sort/paginate are
  applied here; in production the same predicates are pushed to the Cosmos index (§4.1).
- **`McpService`** (`app.py`) — wraps every tool with **auth → rate-limit → meter**
  (via `fbl_auth`) before delegating. This is the testable core.
- **`build_app(cosmos, settings)`** — registers the tools on a `FastMCP("firmenbuch-live")`
  server (Streamable HTTP transport). The transport itself is not unit-tested.

## Run it standalone
```bash
uv run pytest packages/mcp_server
```

## Definition of Done (§8.9) — met
All tools return validated models/envelopes; `has_guv_latest` (and combined) filters
work; unauthorized + rate-limited paths covered; `NotFound` on missing FNR/doc.
`ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
Reads what [`present`](../10_present/README.md) writes to `10_presentation`; authorizes via
[`auth`](../auth/README.md). The serving end of the pipeline.

---
↑ [Repo root](../../README.md) · Specs: [Technische §8.9 / §9](../../docs/specs/Technische_Spezifikation.md)
