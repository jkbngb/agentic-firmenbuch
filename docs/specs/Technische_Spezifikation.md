# agentic-firmenbuch — Technische Spezifikation

**Version:** 1 (everything herein is Version 1 unless explicitly stated otherwise)
**Audience:** the engineer / AI coding tool implementing the system. Written to be read top-to-bottom and executed.
**Companion:** *Fachliche Spezifikation v1* (the "what & why"). This document is the "how".
**Language:** English; accounting/registry **Fachbegriffe in German** (Jahresabschluss, Bilanz, GuV, …).

> **How to use this document.** Each module in §8 has: responsibility, inputs/outputs, public function signatures, idempotency rule, error handling, and a **Definition of Done (DoD)**. Build in the order of §15. Treat the Pydantic models in §6 and the lineage contract in §7 as the source of truth; everything serializes through them. Golden fixtures live in the *Pipeline Step Samples* artifact.

---

## 1. Architecture overview

```
                    ┌──────────────── Azure Container Apps Job (cron, daily) ───────────────┐
 Firmenbuch HVD     │  ingest ──► parse ──► consolidate ──► derive ──► present              │
 SOAP API  ───────► │   │          │            │             │           │                 │
 (free, CC BY 4.0)  │   ▼          ▼            ▼             ▼           ▼                 │
                    └─ 90-raw ── 70-parsed ─ 50_consolidated 30_derived  10_presentation ──────┘
                       (Blob)     (Blob)      (Cosmos)        (Cosmos)    (Cosmos)
                                                                              │ point read / filtered query
                    ┌──────────── Azure Container Apps: MCP server (FastMCP, HTTP) ──────────┐
                    │  tools: search_companies / get_company_details / get_company_history   │
                    │         get_full_record / get_document / list_sectors /               │
                    │         get_cohort_summary / find_peers / describe_fields             │
                    │  auth: X-API-Key header → validate + rate limit + usage metering        │
                    └───────────────────────────────────────────────────────────────────────┘
                       99_registry (watermarks/hashes/runs)   00_accounts (tokens/usage)
```

**Key properties:** scheduled **batch** (not event-driven), **idempotent**, **replayable** from immutable raw, fully **typed**, **modular** (each stage shippable alone), with **reserved layers** (`40_enriched`, `20_scored`) for v2.

---

## 2. Tech stack & conventions

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.12** | one language end-to-end |
| Packaging / env | **uv** (workspace) | fast, lockfiles; one venv per repo |
| Models / validation | **Pydantic v2** | all data contracts are Pydantic models |
| MCP framework | **FastMCP** (`mcp` Python SDK) | Streamable HTTP transport |
| SOAP client | **httpx** + hand-built SOAP envelopes (port the prototype's approach) or **zeep** | the prototype already does raw httpx; keep it, hardened |
| XML parsing | **lxml** | namespace-aware; both filing formats |
| Cosmos | **azure-cosmos** | serverless account |
| Blob | **azure-storage-blob** (ADLS Gen2) | raw + parsed artifacts |
| Secrets | **azure-identity** + **Key Vault** | `DefaultAzureCredential` (Managed Identity in Azure) |
| Email (signup) | **Azure Communication Services** SDK | token delivery |
| Telemetry | **OpenTelemetry** → **Application Insights** | structured logs + metrics |
| Lint / format / types | **ruff**, **ruff format**, **mypy --strict** | CI gates |
| Tests | **pytest** + **pytest-cov** | fixtures = golden samples |
| IaC | **Bicep** | `infra/` |
| CI/CD | **GitHub Actions** + **Azure Container Registry** | build, test, deploy |

**Coding conventions (enforced):** `snake_case` for fields/functions, `PascalCase` for classes; full type hints; no bare `except`; all timestamps **UTC ISO-8601 `…Z`**; money as Python `float`/`Decimal` carrying `currency` once per filing; deterministic, side-effect-free derivations; every public function has a docstring stating inputs/outputs/idempotency.

---

## 3. Repository layout (`agentic-first` monorepo, uv workspace)

The repo is the **`agentic-first`** umbrella: source-agnostic **shared** packages under
`packages/`, one **product** per source under `products/`. The Austrian pipeline is
`products/agentic-firmenbuch/`; a second register (Germany, `agentic-unternehmensregister`)
lives in a **separate private repo** that consumes `packages/` (see Appendix R + §3.5).

```
agentic-first/                       # repo root (uv workspace)
├── pyproject.toml                   # workspace: members = packages/* + products/agentic-firmenbuch/packages/*
├── packages/                        # SHARED — source-agnostic, zero Firmenbuch/UGB knowledge
│   ├── core/   (fbl_core)           # lineage/meta + metric contracts, config, storage clients
│   │   └── src/fbl_core/
│   │       ├── models/              # meta.py, metric.py  (source-agnostic contracts only)
│   │       ├── lineage.py           # uuid, content_hash, lineage helpers
│   │       ├── storage/             # blob.py, cosmos.py, base.py (Protocols), memory.py (fakes)
│   │       ├── config.py            # settings (pydantic-settings)
│   │       └── logging.py
│   └── auth/   (fbl_auth)           # signup, token issue/validate, rate limit, metering, 00_accounts
├── products/
│   └── agentic-firmenbuch/          # AUSTRIA product
│       ├── packages/
│       │   ├── core_at/ (fbl_core_at)  # UGB mapping, Firmenbuch domain models (filing/company/mcp),
│       │   │                           #   ÖNACE classification, OeNB/EIOPA dirs, austria/formats/esvg
│       │   ├── firmenbuch_client/   # SOAP adapter behind RegisterSource interface (fbl_firmenbuch_client)
│       │   ├── 99_registry/         # LAYER 99_registry — company catalog + watermark (fbl_registry)
│       │   ├── 90_ingest/           # LAYER 90_raw — enumeration + change feed + raw download (fbl_ingest)
│       │   ├── 70_parse/            # LAYER 70_parsed — raw XML -> ParsedFiling (fbl_parse)
│       │   ├── 50_consolidate/      # LAYER 50_consolidated — merge per company (fbl_consolidate)
│       │   ├── 30_derive/           # LAYER 30_derived — ratios/growth/percentiles (fbl_derive)
│       │   ├── 10_present/          # LAYER 10_presentation — gated public doc (fbl_present)
│       │   ├── orchestration/       # the Container Apps Job entrypoint: ingest..present (fbl_orchestration)
│       │   └── mcp_server/          # FastMCP app + tools + auth middleware (fbl_mcp_server)
│       └── tests/                   # AT integration + fixtures (legacy/jab40/pdf samples)
├── infra/                           # Bicep modules
└── docs/                            # both specs + pipeline step samples
```

> **Layer-numbered package dirs.** Each pipeline-stage package directory carries its
> layer-number prefix so the owner of every data layer is obvious. Python module names
> cannot start with a digit, so the **importable** package keeps its `fbl_*` name (e.g.
> dir `70_parse/` → `import fbl_parse`); the number is also a `LAYER` constant in each
> stage package. `core`/`core_at`/`firmenbuch_client`/`orchestration`/`mcp_server`/`auth`
> are un-numbered (not a single data layer).

### 3.5 Shared vs product (the reuse boundary)

The **hard rule**: dependency arrows only ever point **product → shared** (`fbl_core_at` →
`fbl_core`), never back — so a shared package never learns anything Firmenbuch/UGB-specific and
stays reusable by the next product. `core` (infra: lineage/meta + metric, config, storage) and
`auth` are the only genuinely source-agnostic packages today and live in `packages/`. `core_at`
is inherently source-specific. `30_derive` and `mcp_server` are algorithmically source-agnostic
but currently bind to AT-shaped domain models, so they live in the product tagged
*promotion-candidate* (they move to `packages/` once the domain models are abstracted — a later
pass). The full per-package classification is **Appendix R**.

### 3.4 LAYER_MAP — layer ↔ package ↔ store ↔ model ↔ sample

| Layer | Package (dir) | import | Store / container | Pydantic model | Sample (Appendix E) |
|---|---|---|---|---|---|
| `99_registry` | `99_registry` | `fbl_registry` | Cosmos `99_registry` | `RegistryDoc` | §15a.0 doc |
| `90_raw` | `90_ingest` | `fbl_ingest` | Blob `90-raw` | raw `Meta` + manifest | Stage 0 |
| `70_parsed` | `70_parse` | `fbl_parse` | Blob `70-parsed` | `ParsedFiling` | Stage 1 |
| `50_consolidated` | `50_consolidate` | `fbl_consolidate` | Cosmos `50_consolidated` | `ConsolidatedCompany` | Stage 2 |
| `30_derived` | `30_derive` | `fbl_derive` | Cosmos `30_derived` | `DerivedCompany` | Stage 3 |
| `10_presentation` | `10_present` | `fbl_present` | Cosmos `10_presentation` | `PresentedCompany` | Stage 4 |
| `00_accounts` | `auth` | `fbl_auth` | Cosmos `00_accounts` | `Account` | — |

Rule: **pipeline stages never import each other** (they communicate only through
Blob/Cosmos). Shared code lives in `core`; `firmenbuch_client` is the API adapter that
`90_ingest` uses; `orchestration` is the only module that wires the stages together.

### 3.1 Folder & file structure — best-practice requirements (mandatory)
- **Intuitive, scalable naming.** Package = the stage/role it owns (`ingest`, `parse`, …). Inside each package: `src/<pkg>/` for code, `tests/` for its tests, `README.md` at the package root. Modules are named for what they do (`xml_legacy.py`, `xml_jab40.py`, `ratios.py`, `growth.py`, `lineage.py`), not generic (`utils.py`, `helpers.py` are discouraged; if unavoidable, scope them e.g. `parse/_internal.py`). One responsibility per module; files stay reviewable (target < ~400 lines).
- **A `README.md` in every major subfolder** (repo root, each `packages/*`, `infra/`, `tests/`, `docs/`). Each README states: purpose, inputs→outputs, how to run it standalone, key files, and its place in the pipeline.
- **READMEs are inter-navigable.** Every README links **up** to the repo-root README and **across** to the previous/next pipeline stage (e.g. `parse/README.md` links to `ingest/README.md` and `consolidate/README.md`) and to the two specs in `docs/`. The root README contains the master index (a table linking to every package README, in pipeline order `90→10`). Use relative Markdown links so they work on disk and in the repo browser.
- **Consistent file conventions:** `snake_case.py` modules, `PascalCase` classes, `test_*.py` tests mirroring the module name, `README.md` (never `readme.txt`), one `pyproject.toml` per package + the workspace root.

### 3.2 Self-contained, closed project (mandatory)
This is a **brand-new, fully self-contained repository**. It must build and run with **zero references to any external project or prior codebase** or any path outside its own folder.
- You **may reuse the prototype's logic** — the enumerator, the XML parsing, the consolidation, the ratio math, the position mapping — but only by **copying the relevant code into this repo and rewriting/adapting it** to the new module layout, schemas, and lineage contract. **Copy, never import or symlink** the prototype.
- After copying, **cut every external reference**: no `from collector...`, no `sys.path` hacks to a parent project, no reading the prototype's `.env`/data. Everything the project needs lives inside the repo (its own `core`, its own config, its own fixtures).
- The acceptance test for "closed": the repo can be zipped, moved to a clean machine, `uv sync`-ed, and all unit tests pass **with the prototype folder absent**.

### 3.3 Module responsibilities (build order)
Each stage is an independent, testable module with one responsibility: `firmenbuch_client`
(HVD SOAP client — `X-API-KEY` auth, retry/429, change-feed methods) → `ingest` (enumeration +
raw archival) → `parse` (XML → canonical positions, incl. the semantic-`jab40` variant, §15b-2)
→ `consolidate` (merge filings + master into one record with lineage/supersedes) → `derive`
(ratios, caps, percentiles — Appendix C) → `present` (Firmenbuch-only served projection +
GDPR gating + attribution). Build in pipeline order; each stage's Definition of Done gates the
next (§15).

---

## 4. Azure infrastructure (Bicep, `infra/`)

### 4.0 One-command, idempotent setup + region policy (mandatory)
- **Single setup script** `infra/setup.sh` (wrapping `az deployment sub create` against the Bicep) brings the whole environment up from nothing: `./infra/setup.sh`. It is **idempotent** — Bicep is declarative, so re-running **creates only what's missing and leaves existing resources untouched** (no duplicates, no errors on re-run). The script first checks for the resource group / each resource and **skips** anything already present, logging "exists, skipping".
- **Region policy (EU-only, ordered fallback):** deploy to **`germanywestcentral` (Germany West Central)** first; if unavailable for a resource/quota, fall back to **`westeurope` (Netherlands)**; if both fail, **`northeurope` (Ireland)**. Never deploy outside the **EU**. The region is a single Bicep parameter `location` with this ordered fallback encoded in `setup.sh`; **all** resources (Storage, Cosmos, Container Apps, Key Vault, ACR, App Insights, Communication Services) go to the chosen EU region. (EU-only matters for GDPR/data-residency given the personal data in §5/§8.7.)
- Re-running after a partial failure resumes cleanly; tearing down is a separate explicit `infra/teardown.sh`.

Provision (one Bicep module per resource, parameterized by environment, `location` per the policy above):
- **Storage account** (ADLS Gen2) with Blob containers `90-raw`, `70-parsed`.
- **Cosmos DB** (serverless) database `firmenbuch`, containers (partition key in parentheses):
  - `50_consolidated` (`/fnr`), `30_derived` (`/fnr`), `10_presentation` (`/fnr`)
  - `99_registry` (`/fnr`), `00_accounts` (`/token_hash`)
  - reserved (create when needed): `40_enriched` (`/fnr`), `20_scored` (`/fnr`)
- **Container Apps Environment** + **Container Apps Job** (cron schedule, e.g. `0 3 * * *` Europe/Vienna) for the pipeline, and a **Container App** (HTTP, min replicas 0–1) for the MCP server.
- **Azure Container Registry** (images), **Key Vault** (secrets), **Application Insights + Log Analytics**, **Azure Communication Services** (email).
- **Managed Identity** for the Job and MCP app; grant Cosmos/Blob/Key Vault data-plane roles. **No keys in code.**

### 4.1 Cosmos indexing policy (serving containers)
On `10_presentation`, index exactly the fields `search_companies` filters/sorts on; exclude large nested histories from indexing to control RU. Indexed paths (illustrative):
```
/identity/status/?          /identity/legal_form/?      /location/bundesland/?      /size/gkl/?
/financials/has_guv/?       /financials/has_guv_latest/?
/financials/latest/bilanzsumme/?   /ratios/equity_ratio/latest/?
/financials/latest/revenue/?       /company/last_filing_year/?
/employees/latest/?         /growth/profile/?
excludedPaths: /financials/bilanz/*  /financials/guv/*  /ratios/*/history/*  /_meta/*
```
> The `present` stage **denormalizes** the handful of filter fields to stable shallow paths (e.g. `financials.latest.bilanzsumme`) so the index stays small and queries are cheap.

---

## 5. Data stores & layer numbering

| Layer | Store | Container | Grain | Written by |
|---|---|---|---|---|
| `99_registry` | Cosmos | `99_registry` | 1 doc / `fnr` (+ a singleton watermark doc) | sync-registry / orchestration |
| `90_raw` | Blob | `90-raw` | `fnr/stichtag/{file}.xml` and `.pdf` + `_manifest.json` | ingest |
| `70_parsed` | Blob | `70-parsed` | `fnr/stichtag.json` | parse |
| `50_consolidated` | Cosmos | `50_consolidated` | 1 doc / `fnr` | consolidate |
| `30_derived` | Cosmos | `30_derived` | 1 doc / `fnr` | derive |
| `10_presentation` | Cosmos | `10_presentation` | 1 doc / `fnr` | present |
| `00_accounts` | Cosmos | `00_accounts` | 1 doc / account (auxiliary, MCP signup) | auth |
| *`40_enriched`*, *`20_scored`* | Cosmos | reserved | — | v2 |

Descending numbers = pipeline order. **`99_registry` is the foundation** — the complete list of every company; it precedes raw ingestion (`90`) and drives everything. `00_accounts` is auxiliary (MCP user accounts, not part of the data pipeline). Gaps reserved for v2 layers. Blob names use hyphens, Cosmos uses underscores.

### 5.1 Data-preservation guarantee — nothing from the API is ever lost
- **`90_raw` is the lossless, immutable, complete system of record.** Every artifact the API returns is stored **byte-for-byte and never modified or deleted**: each Jahresabschluss **XML *and* PDF**, plus the **raw `auszug`, `sucheFirma`/`sucheUrkunde`, and change-feed responses** for each company (e.g. `90-raw/{fnr}/master/auszug_{date}.xml`, `…/_responses/`). If the API gave it to us, it is in `90-raw` verbatim.
- **Strict no-loss carry-forward — each layer is an information SUPERSET of the one before it (Part B):**
  `90_raw` (byte-for-byte, incl. ALL API responses) → `70_parsed` (EVERY recognized canonical from the full 317-entry taxonomy in `positions`, ALL unknown codes in `field_provenance.passthrough`, plus every filing field: GJ/VOR_GJ, WERT_TSD, EINSTUFUNG, signer, employees, names) → `50_consolidated` (the COMPLETE per-company record — `financials.positions` carries *every* position's full year history, not just the typed Bilanz/GuV subset, and `financials.passthrough` carries every unknown code's history; nothing reduced) → `30_derived` (= consolidated **+** computed ratios/growth, never fewer fields) → `10_presentation`.
- **`10_presentation` MAY be a curated projection, but under three rules:** (a) an explicit, justified **allowlist** of exactly which derived fields are intentionally NOT surfaced lives in `fbl_present.PRESENTED_ALLOWLIST` (officer names per GDPR §8.7; the full `positions`/`passthrough` maps; `completeness`; `guv_years`; `signatories_history`; `derivations`; the internal `meta` chain); (b) the **full consolidated/derived record is retrievable** via the MCP `get_full_record` tool (every position + history + passthrough; officer names stay withheld unless `expose_personal_data`); (c) **nothing may be dropped that is not on that allowlist** — enforced by the automated layer-completeness test (`tests/test_layer_completeness.py`), which walks every leaf field/position/code at each layer and fails on any silent loss, including a raw→parsed check that every BETRAG-bearing element is either mapped to a canonical or captured in passthrough (zero unaccounted). The **master path is audited too**: every data-bearing element/attribute of a raw `auszug` response must be captured into `MasterData` or be on a documented allowlist (`test_calls.py::test_auszug_master_path_accounts_for_every_field`) — this is what caught the dropped court, manager role, and EUID — and a master→presentation carry-through test asserts `legal_form`/`court`/`location`/Geschäftszweig/manager survive to the served doc.
- **No silent drops in `parse`:** recognized positions map to canonical names; **every unrecognized value-bearing element is carried through the passthrough** — `XXX_*` codes AND non-`HGB_` free-text slots (`FREI*`/`FREIER_SUB_POSTEN`/`GEB_BEFREIUNG`) that carry real amounts — never dropped; parse failures dead-letter with the raw retained. So even within the projection, unknown content is preserved, not lost. (Confirmed by a 100-company live validation: zero unaccounted value elements.)
- **Net guarantee:** the raw filing is preserved in full forever; every downstream value is traceable back to it via the lineage chain (§7) and to its official UGB code via `source_codes`/`paragraph_ref` (Part A); re-deriving from raw reproduces or extends any layer without re-fetching from the API.

### 5.2 Extensibility — adding data sources later, applied to ALL companies (not just new ones)
Designed in from day one so v2 sources (Northdata ownership, SerpAPI presence, NACE, scoring, …) drop in without rework **and backfill the entire existing universe**:
- **Reserved layers in the numbering gaps:** a new source becomes its own layer (e.g. `40_enriched` between consolidated and derived; `20_scored` before present). The served schema already carries reserved `enrichment`/`score`/`sector`/`summary` fields (null in v1), so adding them is **additive and non-breaking**.
- **Universe-wide backfill is a first-class operation.** Because every company lives in `99_registry` keyed by FNR and every layer is rebuildable, adding a source = (1) run the new enrichment stage over the **whole registry** (a `backfill-<stage>` mode, same pattern as the initial load), then (2) re-run `derive`→`present` over **all** companies so the new data flows into every existing record. A registry-wide **"mark all dirty"** trigger (with a `reprocess_reason`) drives this.
- **Versioning makes it auditable:** `enrichment_version` / bumped `data_version` on every rebuilt doc record which source-set produced it; the lineage `inputs[]` gains the new source's provenance ref. Existing companies and new companies go through the identical path, so coverage is uniform.
- **Idempotent + incremental after backfill:** once backfilled, the new source refreshes per-company on the normal daily cadence (or its own schedule), exactly like filings.

> In short: **yes** — raw is fully preserved, and **yes** — a later source is applied to existing companies too, via a universe-wide reprocess, not only to newly-arriving ones.

---

## 6. Canonical data model (`core/models`, Pydantic v2)

> These are the contracts. All stages serialize through them. Field names are canonical (decoupled from source XML).

```python
# metric.py — the uniform metric object used for every time series
class MetricSeries(BaseModel):
    latest: float | None = None
    latest_year: int | None = None
    history: dict[int, float] = {}                 # {year: value}
    # Official UGB code(s) this line item was parsed from — HGB_*/XXX_* (legacy/fb2025) or
    # the JAb 4.0 element name (jab40). Empty for computed series (ratios). §-traceability (Part A):
    source_codes: list[str] = []
    source_codes_by_year: dict[int, list[str]] = {}  # per-year, ONLY when codes differ across years
    paragraph_ref: str | None = None               # human §-ref, e.g. "§224 Abs 2 A II"
    annual_growth_rates: dict[int, float] = {}     # {year: yoy}
    growth_1y: float | None = None
    growth_3y_cagr: float | None = None
    growth_5y_cagr: float | None = None
    growth_avg_yearly: float | None = None
    growth_volatility: float | None = None
    growth_min_year: float | None = None
    growth_max_year: float | None = None
    # ratios additionally use these (absolutes leave them None):
    avg_3y: float | None = None
    avg_5y: float | None = None
    min_5y: float | None = None
    max_5y: float | None = None
    trend: Literal["improving", "stable", "declining"] | None = None

# meta.py — lineage / provenance (see §7)
class LineageRef(BaseModel):
    stage: str
    doc_id: str
    content_hash: str
    created_at: str            # ISO-8601 Z
    producer: str | None = None
    entity_id: str | None = None

class Meta(BaseModel):
    doc_id: str                # uuid4 of THIS document
    entity_id: str             # "093450b" or "093450b/2025-12-31"
    stage: Literal["raw","parsed","consolidated","derived","presented"]
    producer: str              # "parse@1.0.0"
    source: str = "justizonline_firmenbuch_hvd"
    license: str = "CC-BY-4.0"
    schema_version: str = "1.0"
    metrics_version: str | None = None
    run_id: str
    data_version: int | None = None
    content_hash: str | None = None        # filled last; see §7
    timestamps: dict[str, str] = {}        # {"ingested_at": "...Z", "parsed_at": "...Z", ...}
    checks: dict[str, bool] = {}
    lineage: list[LineageRef] = []         # linear upstream chain
    inputs: list[LineageRef] = []          # fan-in (consolidate): many parsed + master
    supersedes: LineageRef | None = None   # previous version of this entity's doc

# filing.py — one parsed filing (70_parsed)
class Bilanz(BaseModel):
    bilanzsumme: float | None = None
    eigenkapital: float | None = None
    verbindlichkeiten: float | None = None
    anlagevermoegen: float | None = None
    umlaufvermoegen: float | None = None
    sachanlagen: float | None = None
    finanzanlagen: float | None = None
    vorraete: float | None = None
    forderungen: float | None = None
    cash: float | None = None
    rueckstellungen: float | None = None
    stammkapital: float | None = None
    kapitalruecklagen: float | None = None
    gewinnruecklagen: float | None = None
    bilanzgewinn_verlust: float | None = None

class GuV(BaseModel):
    revenue_basis: Literal["umsatzerloese", "rohergebnis"] | None = None
    umsatzerloese: float | None = None
    rohergebnis: float | None = None
    personalaufwand: float | None = None
    abschreibungen: float | None = None
    ebit: float | None = None
    ebitda: float | None = None
    jahresueberschuss: float | None = None

class Signatory(BaseModel):            # name is gated downstream; age/birth_year are exposed (§ compliance)
    first_name: str | None = None      # parsed internally, NOT served publicly
    last_name: str | None = None       # parsed internally, NOT served publicly
    birth_year: int | None = None      # YEAR ONLY — never store/serve full date or month/day
    age_at_signing: float | None = None  # = (signature_date − birth_date)/365.25, 1 decimal (proven in prototype)
    signed_at: str | None = None       # signature_date (DAT_UNT)
    role_code: str | None = None       # PERS_KENN (A/B/C/D); may be a sibling list → positional fallback
# Parse note: the raw filing carries a full GEB_DAT (birth DATE). We compute age_at_signing
# and derive birth_year, then DISCARD the day/month — only year + age are retained anywhere.

class ParsedFiling(BaseModel):
    fnr: str
    stichtag: str                       # "YYYY-MM-DD"
    gj_beginn: str | None = None
    gj_ende: str | None = None
    currency: str = "EUR"
    format: Literal["legacy_finanzonline", "jab40", "pdf"]
    parsed: bool
    has_bilanz: bool = False
    has_guv: bool = False
    bilanz: Bilanz = Bilanz()
    guv: GuV | None = None
    positions: dict[str, float] = {}            # EVERY recognized canonical (full taxonomy), not just typed Bilanz/GuV
    positions_prior_year: dict[str, float] = {} # BETRAG_VJ per canonical — reconciliation only (§15b-8)
    position_codes: dict[str, list[str]] = {}   # canonical -> official code(s) it was parsed from (Part A)
    employees: int | None = None
    signatory: Signatory | None = None
    field_provenance: dict             # {"format","mapping_version","scaling","map","passthrough": {...}}
    meta: Meta

# company.py — consolidated / derived / presented share a base
class ConsolidatedCompany(BaseModel):
    identity: Identity
    location: Location
    company: CompanyMaster
    size: Size
    financials: Financials             # has_guv/has_guv_latest/guv_years + bilanz/guv MetricSeries
    employees: MetricSeries | None = None
    management: Management | None = None      # gated at present
    filings: list[FilingRef] = []
    events: list[RegisterEvent] = []
    # reserved (None in v1)
    sector: None = None
    enrichment: None = None
    score: None = None
    summary: None = None
    observations: None = None
    meta: Meta

class DerivedCompany(ConsolidatedCompany):
    ratios: Ratios
    growth: Growth
    derivations: Derivations
```

`Financials` carries the GuV rollups: `has_guv: bool`, `has_guv_latest: bool`, `guv_years: list[int]`, `revenue_basis`, `completeness: dict[int, dict[str,int]]`, plus `bilanz: dict[str, MetricSeries]` and `guv: dict[str, MetricSeries]` (ergonomic typed views), **and the strict no-loss superset `positions: dict[str, MetricSeries]` (EVERY recognized canonical, keyed by canonical) + `passthrough: dict[str, MetricSeries]` (every unknown source code)** so consolidated/derived reduce nothing (Part B).

---

## 7. Lineage contract (`core/lineage.py`)

```python
def new_doc_id() -> str:                      # uuid4
    return str(uuid.uuid4())

def content_hash(payload: dict) -> str:
    """sha256 over canonical JSON of the document content, EXCLUDING the whole meta block.
    Exclude the ENTIRE meta/_meta envelope — not just {content_hash, timestamps, lineage,
    inputs}. The envelope also carries doc_id (a fresh uuid4 every run), data_version, and
    supersedes (the prior doc_id), all of which are per-run / per-version: hashing any of
    them would change the hash on every rebuild and defeat "same content ⇒ same hash".
    The remaining meta fields (checks, producer) are derived deterministically from the
    content, so excluding them removes nothing that discriminates content.
    Canonical = json.dumps(obj, sort_keys=True, separators=(',',':'), ensure_ascii=False).
    Returns 'sha256:<hex>'. Stable: identical content -> identical hash (idempotency)."""

def stamp(meta: Meta, payload: dict, *, stage_time_key: str) -> Meta:
    """Set meta.timestamps[stage_time_key]=now_utc_z(); then meta.content_hash=content_hash(payload)."""
```

Rules (enforced in code + tested):
- `doc_id` = fresh uuid4 at each stage; never reused.
- Each downstream doc copies upstream `{stage, doc_id, content_hash, created_at}` into `lineage` (linear) or `inputs` (fan-in at consolidate).
- `content_hash` is computed **after** the data is final and **excludes the entire `meta`/`_meta` block** — including the per-run `doc_id` and the per-version `data_version`/`supersedes` — so re-running unchanged input yields the same hash → change detection & skip-unchanged. (Excluding only `{content_hash, timestamps, lineage, inputs}` would hash `doc_id`/`data_version`/`supersedes` and break idempotency.)
- `timestamps` accumulate across stages (`ingested_at` → `presented_at`).
- On rebuild of a consolidated entity, set `supersedes` to the prior doc's `{doc_id, content_hash, data_version}` and increment `data_version`.
- `field_provenance.map` (parsed stage only) records `canonical_field → source XML path`.

See *Pipeline Step Samples* for a full chained example.

---

## 8. Modules (each: responsibility, I/O, signatures, idempotency, errors, DoD)

### 8.1 `core`
**Responsibility:** models (§6), canonical mappings, lineage helpers (§7), config, storage clients, logging. No business logic.
**Storage clients:**
```python
class BlobStore:
    def put_raw(self, fnr: str, stichtag: str, filename: str, data: bytes) -> str          # returns blob path
    def put_json(self, container: str, path: str, obj: dict) -> str
    def get_json(self, container: str, path: str) -> dict | None
class CosmosStore:
    def upsert(self, container: str, doc: dict) -> None      # doc must include 'id' and 'fnr'
    def get(self, container: str, fnr: str) -> dict | None
    def query(self, container: str, sql: str, params: list[dict]) -> Iterator[dict]
```
**DoD:** models round-trip (serialize/parse) the golden fixtures; `content_hash` is stable across runs for identical input; mappings cover every canonical Bilanz/GuV field for both formats.

### 8.2 `firmenbuch_client`
**Responsibility:** typed access to the HVD SOAP API behind a stable interface. Port/harden the prototype's `soap_client.py`.
```python
class RegisterSource(Protocol):
    def suche_firma(self, firmenwortlaut: str, *, suchbereich: int = 1,
                    rechtsform: str = "") -> list[FirmaResult]: ...
    def suche_urkunde(self, fnr: str) -> list[UrkundeRef]: ...           # filings list (Code 48 etc.)
    def urkunde(self, key: str) -> UrkundeContent: ...                   # bytes + format detect
    def auszug(self, fnr: str) -> AuszugKurz: ...                        # master: name, address/Sitz, Rechtsform, court (HG), Geschäftszweig, EUID, persons (+role+Vertretungsart via FUN/PNR), events
    def veraenderungen_urkunden(self, von: date, bis: date) -> list[DocChange]: ...
    def veraenderungen_firma(self, von: date, bis: date) -> list[FirmaChange]: ...

class JustizOnlineClient(RegisterSource): ...   # concrete impl
```
**Idempotency:** read-only. **Errors:** wrap HTTP/SOAP faults in `FirmenbuchApiError`; honor HTTP 429 with exponential backoff (already in prototype); never crash the batch — surface per-item failures to the catalog dead-letter.
**Auth (confirmed from the prototype):** simple **`X-API-KEY: <key>` HTTP header** (not WS-Security), `Content-Type: text/xml; charset=utf-8`, body wrapped in a plain `soap:Envelope`/`soap:Body`; endpoints are `{api_url}/{sucheFirma|sucheUrkunde|urkunde|auszug_v2|veraenderungenFirma|veraenderungenUrkunden}`; request namespaces are `ns://firmenbuch.justiz.gv.at/Abfrage/<X>Request`.
**Tier caveats (assumptions, §16):** the prototype indicates **`auszug` does not work on the HVD tier** — so `auszug()` may raise; callers must tolerate its absence (master data then comes only from `sucheFirma` + the Bilanz-XML). The **change feeds** (`veraenderungen*`) are **unverified** on this tier; §15a D specifies behavior for both the works and the doesn't-work case.
**Notes:** detect filing `format` from the downloaded XML namespace (`finanzonline.bmf.gv.at` → legacy; `justiz.gv.at/Bilanzierung/v4.0` → jab40; PDF by content-type). `auszug` (if available) returns personal data; keep it isolated and gated.
**DoD:** unit tests with recorded SOAP responses (VCR-style) for all six calls; format detection correct on both sample XMLs; backoff verified.

### 8.3 `ingest` → `90-raw` (+ `99_registry`)
**Responsibility:** discover new/changed filings and fetch raw artifacts.
**Flow (daily run):**
1. Read watermark from `99_registry` (last processed change-feed date).
2. `veraenderungen_urkunden(von, today)` → new Jahresabschluss docs; `veraenderungen_firma(..., per Rechtsform)` → register changes. **The feed API rejects any window > 7 days** (`Der Zeitraum darf 7 Tage nicht überschreiten`), so `detect_changes` slices `[von, today]` into ≤7-day chunks and queries each (a normal daily lookback is one window; a post-outage catch-up is several).
3. For each affected `fnr`: `suche_urkunde` (list filings) + `auszug` (master data); download each **new** filing via `urkunde` (store **both** the structured doc and the PDF sibling when present).
4. Write artifacts to `90-raw/fnr/stichtag/` with a `_manifest.json` (raw `Meta` + artifact metadata, content hashes). **Preserve everything (§5.1):** also archive the raw **`auszug`**, **`sucheFirma`/`sucheUrkunde`**, and **change-feed** responses verbatim (e.g. `90-raw/{fnr}/master/`, `90-raw/_responses/{run_id}/`) — the API payload is never thrown away, only added to.
5. Mark each affected `fnr` **dirty** in `99_registry`. **Advance the watermark to `today` whenever the feed read succeeded** — the watermark is the feed *read position*, not a work-completion marker. Failed companies stay dirty and retry (decoupled, so a single transient failure can't pin the watermark at empty and silently skip the feed after an outage). `von = min(last_change_date, today − delta_lookback_days)`, so after an outage the next run sweeps the whole gap.
Periodic **full reconciliation** (quarterly): enumerate via `suche_firma` to catch anything the feeds missed, and emit the drift report of what the change feed missed.
```python
def run_ingest(run_id: str, *, full_reconcile: bool = False) -> IngestReport: ...
```
**Idempotency:** content-hash each artifact; skip if unchanged. Re-running a day is safe.
**Errors:** per-`fnr` failures → dead-letter entry in catalog; they stay dirty and retry next run (the watermark is independent — step 5).
**Operational hardening (2026-06):** the daily run is **self-bounding** — ingest + `process_set` share a `DAILY_MAX_MINUTES` budget (default 5h, under the 6h Container-Apps replica timeout) and stop cleanly, leaving the rest dirty for the next run (the dirty set IS the checkpoint), so a large backlog or post-outage catch-up can never be hard-killed mid-company. Ingest runs with 8 workers. After the daily delta **and** the quarterly grind, the served `__stats__` materialized view that `list_sectors`/`get_coverage` read (this Cosmos SDK build rejects `GROUP BY`, so taxonomy/coverage are precomputed) is rebuilt via `store_stats`.
**DoD:** a dry-run against recorded responses produces correct `90-raw` manifests + dirty set; the watermark advances on a successful feed read; PDF stored alongside XML.

### 8.4 `parse` → `70-parsed`
**Responsibility:** raw filing XML → `ParsedFiling` (canonical). **Three observed XML variants** must map to ONE canonical position dict (verified against the prototype's `pipeline/shared/position_mapping.py`, which keys every canonical position by both `hgb_codes` and `v4_elements`):
- **`legacy_finanzonline` (v3.x):** positional `HGB_*`/`XXX_*` codes; value at `…/POSTENZEILE/BETRAG`; fiscal year in `GJ/ENDE`.
- **`firmenbuch_2025` (the prototype's "v4.0"):** still `HGB_*` codes but value at `…/BETRAG_GJ`; fiscal year in `GESCHAEFTSJAHR/ENDE`. Detect by presence of `GESCHAEFTSJAHR`.
- **`jab40_semantic`:** the fully-semantic JAb 4.0 elements (`BILANZ_EIGENKAPITAL`, `UMSATZERLOESE`, …) per the XSD. **Confirm whether the 2026-mandatory format actually emits this** — the prototype's extractor only handles `HGB_*`-style tags, so a truly semantic filing would currently be missed (§15b-2).
```python
def parse_filing(raw_xml: bytes) -> ParsedFiling: ...      # auto-detects variant
def detect_variant(root) -> Literal["legacy_finanzonline","firmenbuch_2025","jab40_semantic"]: ...
```
**Rules:** map every source code/element to its **canonical** name via the position-mapping table (covers `bilanz`, `guv_gkv`, `guv_ukv`, `anlage_1/2/3`, `zusatzangaben_mikro_ag`, the Spiegel). Position-code prefixes are `HGB_`, `GUV`, `XXX_` (`XXX_*` = non-standard items, e.g. hybride Finanzinstrumente). Keep **unknown codes** under a passthrough so nothing is silently dropped — this includes **non-`HGB_` free-text slots** (`FREI`/`FREI1`/`FREI3`/`FREIER_SUB_POSTEN`, and admin fields like `GEB_BEFREIUNG`) that filers use for real positions (e.g. *"Gewinnvortrag aus Vorjahren"*, *"erhaltene Anzahlungen"*). Because several free slots reuse the same tag, passthrough is keyed `CODE: <TEXT-label>` (a `#n` suffix breaks any remaining clash) so two `FREI` rows both survive — never overwritten (live-validated, §15b-21). Apply `WERT_TSD` ×1000 when set; join multi-line names (hyphen-glue when a segment ends in `-`); extract `employees` from `HGB_Form_3_16` / `DURCHSCHNITTLICHE_ANZAHL_ARBEITNEHMER`; extract signatories from `UNTER` (`V_NAME`,`Z_NAME`,`TITEL`,`GEB_DAT`,`DAT_UNT`,`PERS_KENN`), compute `age_at_signing`, derive `birth_year`, **discard day/month**; set `has_bilanz`/`has_guv` (GuV present iff any `HGB_231*`/`GUV*`/UKV/GKV position). PDF-only → `ParsedFiling(parsed=False, format="pdf")`, financials empty (document still linked).
**Official code preservation (Part A):** for every recognized position, record the EXACT source identifier it was parsed from in `position_codes[canonical]` — the `HGB_*`/`XXX_*` code (legacy/firmenbuch_2025) or the JAb 4.0 element name (`jab40_semantic`). If **two distinct source codes** in one filing map to the same canonical, **keep BOTH and log a collision** (the value stays from the first; codes are never overwritten). This is the basis for the §-traceable `MetricSeries.source_codes`/`paragraph_ref` downstream (§8.5, Appendix D).
**Robustness (from prototype):** on XML parse error, emit a **stub with an `error` field** (dead-letter), never crash. Use the year's **own `BETRAG`** as authoritative — do **not** trust `BETRAG_VJ` for values (use it only as an optional cross-check). Tolerate malformed `GEB_DAT` (not all are `YYYY-MM-DD`).
**Checks:** `aktiva_equals_passiva` (within tolerance) and `negative_equity` (Eigenkapital < 0 is legal and real) recorded in `meta.checks`.
**Idempotency:** pure function of input bytes.
**DoD:** parses the legacy + firmenbuch_2025 sample XMLs to exact numbers; `XXX_*` and unknown codes preserved; malformed birth dates don't crash; `age_at_signing` matches `_compute_age_at_signing`; GuV-presence correct on a Bilanz-only fixture.

### 8.5 `consolidate` → `50_consolidated`
**Responsibility:** merge all of a company's `ParsedFiling`s + master data into one `ConsolidatedCompany` with per-line **MetricSeries.history** (facts only; no growth yet). `master` comes from `sucheFirma` (always: Sitz, Rechtsform, Gericht) plus `auszug` **only if the tier supports it** (else `None`); Stammkapital + signing Geschäftsführer come from the Bilanz-XML.
```python
def consolidate(fnr: str, filings: list[ParsedFiling], master: MasterData | None,
                prev: ConsolidatedCompany | None) -> ConsolidatedCompany: ...
```
**Rules:** fiscal-year alignment; dedupe resubmissions (keep latest per Stichtag); **prior-year reconciliation** (each filing's prior-year column cross-checks the previous filing); compute `has_guv`, `has_guv_latest`, `guv_years`, `completeness`; build `filings[]` with PDF/XML links; set `meta.inputs` to all parsed+master refs, `meta.supersedes` to `prev`, bump `data_version`.
**Idempotency:** deterministic from inputs; unchanged inputs → identical `content_hash` → upsert is a no-op-equivalent.
**DoD:** multi-year fixture consolidates to correct histories; `supersedes` + `data_version` correct on rebuild; GuV rollups correct when GuV present in only some years.

### 8.6 `derive` → `30_derived`
**Responsibility:** add `ratios`, `growth`, trends, size band, peer percentiles, `derivations` catalog.
```python
def derive(company: ConsolidatedCompany, *, growth_horizons: list[int] = [1,3,5],
           cohort_stats: CohortStats) -> DerivedCompany: ...
def compute_growth(series: MetricSeries, horizons: list[int]) -> MetricSeries: ...   # absolutes
def compute_ratio_series(values_by_year: dict[int,float]) -> MetricSeries: ...        # ratios: avg/min/max/trend
```
**Rules:** absolutes get YoY + N-year CAGR (config `growth_horizons`, default `[1,3,5]`, **no 2/4**); ratios get per-year + rolling avg/min/max/volatility/trend (no CAGR); `peer_percentiles` computed against `cohort_stats` from a **global pass** over the universe (run once per pipeline run); write `derivations.formulas`.
**Idempotency:** deterministic; carries `metrics_version`.
**DoD:** ratio/growth outputs match the prototype's numbers on the `093450b` fixture; toggling `growth_horizons` to `[1,2,3,4,5]` emits `growth_2y_cagr`/`growth_4y_cagr` with no other change.

### 8.7 `present` → `10_presentation`
**Responsibility:** assemble the public document; enforce scope, gating, attribution; denormalize filter fields; upsert.
```python
def present(company: DerivedCompany, *, expose_personal_data: bool = False) -> PresentedCompany: ...
```
**Rules:** keep in-scope groups; reserved groups = `null`; copy `identity.status` from `99_registry` (so the MCP can filter active vs gelöscht); **management gating** — the `expose_personal_data` gate (default False in code) stays supported for reverting, but **PRODUCTION RUNS WITH `expose_personal_data=true` since the owner decision of 2026-06-24: officer NAMES ARE served** (public Firmenbuch data, per-query lookup). **`age_at_signing`, current `age`, and `birth_year` (year only) ARE exposed** for the primary manager / signatories, alongside `n_signatories_latest` and `signatories_stable_years`. Birth data stays **year-only** — never month or day, in any mode. (Rationale: names are public register data lawfully published under the Firmenbuchgesetz; bulk extraction/resale is barred by the usage terms. Year-of-birth without month/day remains the GDPR minimization for birth data.) add public `provenance` (source, license, attribution, data_version, built_at) and **omit internal hash chain** from the served body; denormalize `identity.status`, `financials.latest.*`, `ratios.equity_ratio.latest`, `has_guv_latest`, etc. to indexed shallow paths.
**Status-only refresh:** when a company is dirty solely due to `dirty_reason="status_change"`, re-run `present` from the existing `30_derived` doc (no re-parse/derive) to update `status` cheaply.
**Idempotency:** upsert by `fnr`; `id == fnr`.
**DoD:** served doc validates against the MCP response model; no officer names present when gating on; attribution present; indexed filter fields populated.

### 8.8 `orchestration`
**Responsibility:** the Container Apps Job entrypoint. Dispatches on `--mode`, holds the **singleton run lock**, and runs one pipeline pass. See the runbook (§15a) for the two regimes (Initial Load vs Operation) and the concurrency rules.
```python
def main(mode: str) -> int:                       # sync-registry | backfill-ingest | backfill-process | daily
    run_id = make_run_id(mode)                    # "2026-06-16-daily-0003"
    with run_lock(catalog, run_id) as acquired:   # lease+heartbeat in 99_registry
        if not acquired:                          # a previous run is still going
            log.info("run lock held, exiting"); return 0
        # sync_registry = idempotent upsert/diff of 99_registry vs the authoritative
        # bulk dataset. First run on an empty registry = full SEED; later runs = RECONCILE.
        if mode == "sync-registry":   return sync_registry(run_id)
        if mode == "backfill-ingest": return ingest_all(run_id)            # I-1 (registry-driven)
        if mode == "backfill-process":return process_set(run_id, catalog.all_fnrs())  # I-2
        # mode == "daily":
        run_ingest_delta(run_id)                  # change-feed OR rolling re-scan -> marks dirty
        process_set(run_id, catalog.dirty_fnrs())
        advance_watermark(run_id)                 # only on full success
        return 0

def process_set(run_id, fnrs) -> int:
    cohort = build_cohort_stats()                 # global pass for percentiles (once per run)
    for fnr in fnrs:                              # stages run SEQUENTIALLY per company
        try:
            cons = consolidate(fnr, parse_all(fnr), load_master(fnr), prev=load_prev(fnr)); store(cons)
            der  = derive(cons, cohort_stats=cohort); store(der)
            present_and_store(der)
            catalog.mark_clean(fnr)
        except Exception as e:                     # one bad company never fails the run
            if is_throttle(e):                     # transient Cosmos 429 → RETRYABLE, never dead-lettered:
                report.throttled += 1              #   leave the FNR un-done so the next run retries it
                continue                           #   (else the serverless RU ceiling permanently drops it)
            catalog.dead_letter(fnr, e)
    return 0
```
**Throttling (serverless Cosmos):** the Cosmos client is built with `retry_total=30, retry_backoff_max=60`, and the bulk `backfill-process` runs at a modest `PROCESS_WORKERS` (4 on serverless) so the per-partition RU ceiling isn't blown. A 429 that still surfaces is counted (`ProcessReport.throttled`) and left for the next run — **a transient throttle must never be confused with a data failure** (a 12-worker run once dead-lettered ~16k healthy GmbHs this way and they had to be reprocessed).
**Concurrency:** the `run_lock` guarantees **no two runs overlap**; the daily Job is configured to a single instance; cron does **not** chain runs — each is independent, coordinated by the watermark. Stages are sequential within a run (no inter-stage race).
**Idempotency:** safe to re-run a `run_id`; only dirty companies rebuilt; watermark advances last and only on success.
**DoD:** end-to-end run on fixtures produces a valid `10_presentation` doc; re-run with no new data is a no-op (hashes unchanged); a second concurrent invocation exits via the lock without doing work.

### 8.9 `mcp_server`
**Responsibility:** FastMCP app exposing the tools (§9), reading `10_presentation`. Auth middleware validates the token and enforces rate limits before any tool runs.
```python
mcp = FastMCP("firmenbuch-live")

@mcp.tool()
def search_companies(filters: SearchFilters, sort: Sort | None = None,
                     page: int = 1, page_size: int = 25) -> SearchResponse: ...
@mcp.tool()
def get_company_details(fnr: str) -> CompanyDetail: ...
@mcp.tool()
def get_company_history(fnr: str, metrics: list[str] | None = None) -> HistoryResponse: ...
                                                                  # each metric carries source_codes + ugb_paragraph (Part A)
@mcp.tool()
def get_full_record(fnr: str) -> dict: ...                        # full consolidated/derived superset (Part B §5.1); names gated
@mcp.tool()
def get_document(doc_key: str) -> DocumentResponse: ...            # link or bytes to PDF/XML
@mcp.tool()
def list_sectors() -> TaxonomyResponse: ...                       # legal-form + size taxonomy (v1)
@mcp.tool()
def get_cohort_summary(dimension: str, value: str) -> CohortResponse: ...
@mcp.tool()
def find_peers(fnr: str, n: int = 10) -> PeersResponse: ...        # optional v1
@mcp.tool()
def describe_fields() -> dict: ...                                 # self-describing field catalog (all tiers + codes + null rules)
```
**Three data tiers (deliberate — list/detail/full separation is API best practice).**
`search_companies` returns a **compact summary card** (10 fields) for ranking/scanning — *not*
the full record; `get_company_details` returns one company's full served profile;
`get_full_record` returns the superset (full 317-position taxonomy + lineage). Agents escalate
card → profile → full record. `describe_fields` exposes the whole catalog (every field per
tier, the bundesland/gkl/legal_form code tables, and the availability rules — when a field is
null: GuV-only-when-`has_guv`, `employees` often null, `growth` needs ≥2 years, names withheld
by GDPR) so the shape is **discoverable, not guessed**. The same catalog is published for humans
at `felder.html` (`docs/FIELD_REFERENCE.md`), referenced from the tool descriptions.
**Auth:** the API key is read from the `X-API-Key` **request header** (set at `claude mcp add
… --header`), never a tool argument — it never appears in a tool-call payload.

**Response envelope:** every response includes `{schema_version, data_version, results|result, provenance}`. Cap `page_size`; default sort stable.
**Errors:** typed errors (`NotFound`, `Unauthorized`, `RateLimited`, `BadRequest`) mapped to MCP error responses.
**DoD:** all tools return validated models; `has_guv`/`has_guv_latest` filters work against Cosmos index; unauthorized/ratelimited paths covered by tests.

### 8.10 `auth`
**Responsibility:** signup → token; validate; rate-limit; meter usage.
```python
def signup(email: str) -> None                         # creates account, emails token (via ACS)
def issue_token(email: str) -> str                     # opaque; store sha256(token) in 00_accounts
def validate(token: str) -> Account | None
def check_rate_limit(account: Account) -> RateDecision  # per-minute + per-day, config-driven
def record_usage(account: Account, tool: str) -> None
```
**Data:** `00_accounts` doc `{id, token_hash, email, tier, status, created_at, usage}`. Tier + quotas are config so a paid tier is a config change. Tokens stored **hashed**.
**DoD:** signup issues a working token; limits enforced; tokens never stored in plaintext.

### 8.10a Client-Auth-Matrix (live-verified 2026-06-23)
The MCP server speaks **streamable HTTP** at `/mcp` and authenticates via the **`X-API-Key`** request header. Which clients can use that header is a function of the client, not the server. **Confirmed empirically against the deployed server with a real key:**

| Client | Reads which config | Header-auth (key) supported? | Status |
|---|---|---|---|
| **Claude Code** (CLI + Desktop "Code" tab) | `~/.claude.json` (`claude mcp add --scope user`) | yes | ✅ live-verified (calls return) |
| **GitHub Copilot / VS Code** | `mcp.json` (or `code --add-mcp`) | yes | ✅ |
| **Cursor** | `mcp.json` | yes | ✅ |
| **Claude Cowork** (Desktop "Cowork" tab) | sandboxed VM; only Settings → Connectors | **no** | ❌ needs OAuth (§8.10b) |
| **claude.ai** (web) | Settings → Connectors | **no** | ❌ needs OAuth (§8.10b) |

**Why Cowork/claude.ai cannot use the key path** (Anthropic Support docs + our own test): Cowork runs in an Anthropic-managed sandbox VM and **does not read `~/.claude.json` or `claude_desktop_config.json` at runtime**. Its only supported MCP attachment is the remote-connector flow (URL + Login), and that UI does not expose a free-form header field. A `claude_desktop_config.json` mcp-remote bridge is loaded by the Desktop **Chat** tab but **not** by Cowork. Our test added the server via every CLI scope and Desktop config form — Cowork still saw only its built-in tools.

**Consequence — this is an architecture constraint, not a server bug:** to support Cowork and claude.ai we must add the MCP OAuth 2.1 flow (§8.10b). Until then, end-user-facing copy (email, onboarding, README) says exactly that, and offers only the working clients (Claude Code, Copilot, Cursor) with the API-key path.

### 8.10b MCP OAuth 2.1 (IMPLEMENTED + live-verified 2026-06-23 — Cowork/claude.ai unblock)
Status: built and deployed. `fbl_auth.oauth` holds the model/storage; the endpoints live on the
FastMCP app (`fbl_mcp_server.app`). The full chain was verified live against
`https://mcp.agentic-firmenbuch.at`: DCR → /authorize (email magic-link) → confirm → code →
/token (PKCE S256) → Bearer → an authenticated `search_companies` call returned results. The
existing X-API-Key path is unchanged (`McpService._authorize` tries the API key, then the bearer
token; both resolve to the same `Account`, so rate-limit + metering are identical). Login is a
**magic-link**: the user enters their email at /authorize, gets a 15-min confirm link, clicking it
issues the code (= consent). New users are auto-created (`get_or_create_account_by_email`,
idempotent by email, random account id so the X-API-Key path can never resolve to it). Cosmos:
`00_oauth_clients` / `00_oauth_codes` / `00_oauth_tokens` / `00_oauth_pending` (all /id). The MCP
container carries the ACS secret so it can send the login email.

Design (as built):
**Responsibility:** make the server an OAuth 2.1 authorization server so MCP clients (Cowork, claude.ai, future Connector UIs) can attach by URL + login, with no key copy/paste.

**Discovery trigger (the piece that actually makes Cowork/claude.ai start OAuth).** A connector client that attaches by URL with no credential must be *told* to authenticate, or it just sees a 200 and reports "could not connect to a valid MCP server" (this was the live failure on 2026-06-23). Per the MCP June-2025 auth spec + RFC 9728 the server therefore:
* returns **HTTP 401** to any unauthenticated `/mcp` request, carrying `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource/mcp"`;
* serves **`GET /.well-known/oauth-protected-resource[/mcp]`** (RFC 9728) → `{resource, authorization_servers:[<base>], scopes_supported, bearer_methods_supported:["header"]}`, which points the client at the authorization-server metadata below.
This is implemented as a thin ASGI wrapper (`fbl_mcp_server.app._OAuthChallenge`, served by `build_asgi_app`) that challenges **only when neither `X-API-Key` nor `Authorization: Bearer` is present** — so the existing header clients (Claude Code/Copilot/Cursor) are never blocked. We deliberately do **not** enable FastMCP's native `auth=`, which would force a Bearer on every request and lock those clients out. Verified live: unauth `POST /mcp` → 401 + correct `WWW-Authenticate`; both well-known docs resolve; an `X-API-Key`/`Bearer` request still returns 200.

Endpoints (MCP spec March 2025 + OAuth 2.1 + DCR):
* `GET /.well-known/oauth-authorization-server` — RFC 8414 metadata; lists `authorization_endpoint`, `token_endpoint`, `registration_endpoint`, supported scopes, PKCE methods (`S256`).
* `POST /register` — RFC 7591 **Dynamic Client Registration**: client posts redirect URIs, we issue a `client_id` (no client_secret needed for public PKCE clients).
* `GET /authorize` — PKCE-protected login UI. User authenticates against `00_accounts` (or via magic-link if not logged in), grants scope `mcp:read`, gets redirected back with an authorization code.
* `POST /token` — exchanges code+verifier → opaque bearer access token (sha256-hashed in store) + refresh token. Tokens are scoped to the account.
* MCP requests use `Authorization: Bearer <token>`; the server validates exactly like the X-API-Key header (same `validate()` → rate-limit → meter pipeline, just a different credential type on the same account).

Storage: `00_accounts.tokens[]` holds hashed bearer tokens with `kind=oauth|api_key`, `expires_at`, `refresh_hash`. Account linking: the OAuth login screen recognises a logged-in account by session cookie; first OAuth grant for a new email auto-creates the account (same email = same account as the API-key signup).

DoD: a fresh Cowork session adds the connector by URL, signs in, and `search_companies` returns results without any key/header copy. Existing X-API-Key clients keep working unchanged.

---

## 9. MCP tool I/O contracts (`core/models/mcp.py`)

```python
class SearchFilters(BaseModel):
    status: Literal["active", "inactive", "all"] = "all"   # both kept; 'inactive' = historical|deleted
    name: str | None = None                # case-insensitive substring match on company name
    legal_form: str | None = None          # accepts "GmbH" → STARTSWITH(legal_form,'GE'); see note below
    bundesland: str | None = None          # accepts full name "Wien" → mapped to stored code "W"
    size_gkl: Literal["W","K","M","G"] | None = None   # W=Mikro/Kleinst, K=Klein, M=Mittel, G=Groß
    bilanzsumme_min: float | None = None;  bilanzsumme_max: float | None = None
    equity_ratio_min: float | None = None; equity_ratio_max: float | None = None
    revenue_min: float | None = None;      revenue_max: float | None = None
    employees_min: int | None = None;      employees_max: int | None = None
    growth_profile: Literal["shrinking","stable","growing","fast_growing"] | None = None
    has_guv: bool | None = None
    has_guv_latest: bool | None = None
    last_filing_year_min: int | None = None
    founded_year_min: int | None = None    # Gründungsjahr ≥ (young-company / ABM discovery)
    founded_year_max: int | None = None    # Gründungsjahr ≤
    gf_age_min: int | None = None          # primary Geschäftsführer current age ≥ (succession screen)
    manager_name: str | None = None        # substring on management.primary_manager_name (served
                                           # only when EXPOSE_PERSONAL_DATA is set; see §8.7 update)
    geschaeftszweig: str | None = None     # substring/keyword on company.description (Geschäftszweig)
                                           # — the industry/activity (branch) filter. PLANNED; see note.
# **Industry/activity (branch) filter — `geschaeftszweig` (PLANNED, P3).** A case-insensitive
# substring match on `company.description` (the Geschäftszweig from the Firmenbuch master extract:
# „Gastgewerbe", „Baustoffhandel", …), populated for ~84% of served companies. Server-side it is a
# `CONTAINS(LOWER(c.company.description), @q)` clause (mirrors the `name` filter); `company.description`
# is added to the indexed paths (§4.1) and surfaced on the CompanyCard. It is **free text, not a
# standardized code**; a coarse NACE/ÖNACE classification mapped at `derive` is a reserved later seam.
# **GISA is NOT used for this** — the open-data dump is anonymized (no FN/name → no join) and the GISA
# API's terms forbid bulk/list queries; see Fachliche Spezifikation §2.4 for the full decision so it
# is not retried. The GISA API stays a possible optional single-record live lookup only.
# CompanyCard also carries `bilanzsumme_band` (human size band — size_gkl is the UGB *filing*
# class, not magnitude) and `manager_name` (null unless EXPOSE_PERSONAL_DATA). get_company_history
# accepts the card name `revenue` as an alias of the stored `umsatzerloese`; get_cohort_summary
# accepts `size_gkl` as an alias of the `gkl` dimension.

# IMPLEMENTATION (service.search_companies): filtering/sorting/paging run **server-side** in
# Cosmos — a parameterized `WHERE … ORDER BY … OFFSET/LIMIT` query touches only one page, never
# the whole ~341k-doc container (a full scan measured ~437s; a server-side page ~0.2s). The
# in-memory test store ignores SQL and returns every doc, so the same predicate is also applied
# in Python (branch on whether COUNT(1) came back an int). Sort field ∈ {bilanzsumme, revenue,
# equity_ratio, employees, last_filing_year}; default bilanzsumme desc.
#
# STORED CODES (10_presentation): location.bundesland is the official 1–2 letter code
# (W=Wien, O=Oberösterreich, St=Steiermark, N, K, S, T, V, B); identity.legal_form is the
# granular Firmenbuch Rechtsform code (GmbH family = the "GE" prefix, GES ≈ 99.7%). The read
# layer maps full names ↔ codes on input (filter) and output (card display).

class CompanyCard(BaseModel):       # compact search result
    fnr: str; name: str; legal_form: str; bundesland: str | None
    size_gkl: str | None; bilanzsumme_latest: float | None
    equity_ratio_latest: float | None; revenue_latest: float | None
    growth_profile: str | None; has_guv_latest: bool

class SearchResponse(BaseModel):
    schema_version: str; data_version_max: int
    total: int; page: int; page_size: int
    results: list[CompanyCard]; provenance: PublicProvenance
```
Error model: `{error: {code, message}}` with codes `not_found | unauthorized | rate_limited | bad_request | internal`.

---

## 10. Configuration & feature flags (`core/config.py`, pydantic-settings)

Environment variables (from `.env` locally, Key Vault in Azure):
```
JUSTIZONLINE_API_URL, FIRMENBUCH_API_KEY
COSMOS_ENDPOINT (+ Managed Identity), BLOB_ACCOUNT_URL
ACS_CONNECTION_STRING (email), APPINSIGHTS_CONNECTION_STRING
LOG_LEVEL
```
Feature flags (config, not code):
```
GROWTH_HORIZONS = [1,3,5]          # add 2,4,10 later without schema change
ENABLE_DETERMINISTIC_SUMMARY = false
ENABLE_OBSERVATIONS = false
EXPOSE_PERSONAL_DATA = false       # GDPR gate for officer names
RATE_LIMIT_PER_MIN = 60            # MCP per-token limit (not the HVD API)
RATE_LIMIT_PER_DAY = 5000
SCHEMA_VERSION = "1.0"
METRICS_VERSION = "1.0"
```
Operational config (pipeline scheduling/concurrency — §15a):
```
DAILY_CRON = "0 3 * * *"           # Europe/Vienna; Operation runs once/day
HVD_MAX_REQUESTS_PER_SEC = 5       # shared token-bucket ceiling across all ingest workers (tune to §16)
INGEST_WORKERS = 8                 # parallel shards for backfill/ingest
PROCESS_WORKERS = 8                 # parallel shards for backfill-process (run at 4 on serverless Cosmos to avoid 429)
PROCESS_RECHTSFORMEN = "GES"        # served-layer scope for the bulk process (GmbH-first; widen later)
RUN_LOCK_TTL_SEC = 14400           # lease length for the singleton run lock (heartbeat-renewed)
DELTA_MODE = "change_feed"         # change_feed | rolling_rescan  (set per §16 tier finding)
ROLLING_RESCAN_DAYS = 14           # if rolling_rescan: recheck the whole universe every N days
REGISTRY_SYNC_CRON = "0 2 1 */3 *" # quarterly sync-registry reconcile (1st of Jan/Apr/Jul/Oct, 02:00 UTC); daily delta is the steady state
DAILY_CRON         = "0 3 * * *"   # daily change-feed delta (03:00 UTC)
```

---

## 11. Observability & coverage dashboard
- **Logs:** structured JSON via OpenTelemetry → App Insights; every stage logs `run_id`, `fnr`, counts, durations.
- **Run metrics:** fetched / parsed / failed / upserted per stage; dead-letter size; watermark position.
- **Alerts:** run failure; coverage drop; parse-failure-rate spike.
- **Coverage dashboard (internal):** universe counts — companies with ≥1 XML filing vs **PDF-only** vs none; parse-success rate by `format` and year. Implement as a small read-only query view (Application Insights workbook or a `/coverage` endpoint on the MCP app, auth-restricted). This directly answers "how many are PDF-only."
- **Operator status command (`scripts/status.sh`):** at-a-glance health of the live run without opening the Portal. Prints the latest Job execution (name, status, start, end, run time) and the `99_registry` totals: **company count, a by-status breakdown (active / historical / deleted / other) with percentages, and a by-rechtsform breakdown** (legal-form code) with percentages. Each breakdown's percentages are relative to **its own** sum — Cosmos cross-partition aggregates run as separate `SELECT VALUE COUNT(1)` queries (no `GROUP BY`), so against a registry that is still being written the two breakdowns are independent live snapshots and may differ by the rows inserted in between (the script annotates the rechtsform snapshot total when it diverges).

---

## 12. Testing strategy
- **Fixtures:** the two provided legacy XMLs, a JAb 4.0 XML (once supplied), a PDF-only example, the `093450b` golden chain (raw→…→presented) from *Pipeline Step Samples*.
- **Unit:** parsers (both formats, WERT_TSD, Aktiva=Passiva), lineage hashing (stability), ratio/growth math (match prototype numbers), GuV rollups, gating.
- **Integration:** end-to-end orchestration on fixtures with the in-memory Blob/Cosmos fakes; idempotency (second run = no changes); incremental (add a new filing → history grows, `data_version` bumps, MCP reflects it).
- **MCP:** tool contracts, filters (esp. `has_guv_latest`), auth + rate-limit paths.
- **True end-to-end (live, `tests/e2e/`):** a small configurable set of real FNRs through **every layer** (`firmenbuch_client → 90_raw → 70_parsed → 50_consolidated → 30_derived → 10_presentation → MCP query`). Separate from the fixture tests and **env-flag-guarded** (`FBL_E2E=1` + a key), so it runs on demand, not in CI. In-memory stores + a tiny real pull — never Azure, never the full backfill.
- **Coverage gate:** CI fails under 80%; `mypy --strict` and `ruff` must pass.

---

## 13. CI/CD (GitHub Actions)
- **PR:** `uv sync` → `ruff` → `mypy --strict` → `pytest --cov`.
- **Main:** build container images (pipeline + MCP) → push to ACR → `az containerapp` / job update via Bicep deploy. Secrets via OIDC to Azure, never in repo.

---

## 14. Security & compliance implementation
- **Secrets** in Key Vault; access via Managed Identity; nothing in code/repo.
- **Attribution** injected into every MCP response `provenance` block (CC BY 4.0).
- **API-only** ingestion (never scrape the portal).
- **GDPR gating:** the design default is `EXPOSE_PERSONAL_DATA=false` (present emits only
  non-identifying derivations; personal fields stay in internal layers `50/30`). **Operational
  status (owner decision, 2026-06-24): the flag is set TRUE in production** — the MCP server and
  the present jobs run with `EXPOSE_PERSONAL_DATA=true`, so the primary manager's **name** is
  served (search filter `manager_name`, on the card, in `get_company_details`/`get_full_record`).
  **Birth data stays year-only** (`birth_year` / `age`); never month or day. Lawful basis: officer
  names are **public Firmenbuch data** intended for per-record lookup; the §16 #6 open item is
  hereby resolved for per-query use. The usage terms bar **bulk extraction/resale** of the personal
  data (Austrian law restricts onward commercial exploitation of Firmenbuch data); rate limits
  (60/min, 5000/day, page ≤100) are the technical backstop. Reverting is just flipping the flag.
- **Data minimization:** discard generator-comment personal data during parse.

---

## 15. Build order (milestones with Definition of Done)

> **Build methodology — incremental, test-on-real-data, stage-by-stage (mandatory).** Do **NOT** write the whole pipeline at once. Build **one stage at a time, as a vertical slice**: write the module + its unit tests, run it on the **golden fixtures**, then on a **small batch of real data** (a handful of real FNRs / filings end-to-end), confirm the output against the previous stage's real output, and only **then** proceed to the next stage. Each milestone below has a **Definition of Done that gates the next** — a stage isn't "done" until its tests pass *and* it has produced correct output on real data. This catches the real-world edge cases (§15b) early, where they're cheap to fix, instead of after a 200k backfill. Commit per stage; keep each stage runnable standalone (`--mode backfill-<stage>`).

1. **`core` + fixtures** — models, lineage, mappings; golden fixtures load. *DoD:* fixtures round-trip; `content_hash` stable.
2. **`parse`** — both formats → canonical, tested to exact numbers on the sample XMLs. *DoD:* §8.4.
3. **`firmenbuch_client`** — six calls, format detection, backoff, recorded-response tests. *DoD:* §8.2.
4. **`infra`** — Bicep: Blob, Cosmos (containers + index policy), Key Vault, ACA env. *DoD:* `az deployment` succeeds in a test sub.
5. **`ingest`** — change-feed delta + watermark + catalog + raw/PDF storage. *DoD:* §8.3.
6. **`consolidate` + `derive`** — multi-year merge + ratios/growth/percentiles. *DoD:* §8.5–8.6.
7. **`present`** — scope/gating/attribution + denormalized index fields. *DoD:* §8.7.
8. **`orchestration`** — end-to-end Job; idempotent; incremental rebuild verified. *DoD:* §8.8.
9. **`mcp_server` + `auth`** — tools + token + rate limit + signup. *DoD:* §8.9–8.10.
10. **Coverage dashboard + alerts**, then scale to full universe (daily incremental + quarterly reconciliation).

---

## 15a. Operations runbook

Everything runs as **Azure Container Apps Jobs**, one image, selected by `--mode`. There are **two clearly separated regimes**: **(1) Initial Load** — a one-off bootstrap you run by hand, exactly once; and **(2) Operation** — the daily steady-state job. They never run at the same time (the daily schedule is enabled only after the Initial Load finishes — see §15a.4).

### 15a.0 The company registry (`99_registry`) — the master list of all companies

> This answers "how do we maintain a list of all companies, and where does it live."

There is **one authoritative list of every company: the `99_registry` Cosmos container**, one document per FNR. It lives in our store (not re-derived per run) and **drives everything** — every download, every rebuild, every reconciliation iterates over or is keyed by this registry. Blob `90-raw` is the source of truth for *documents we have*; `99_registry` is the source of truth for *companies that exist and their processing state*.

Registry document (per FNR):
```jsonc
{
  "id": "093450b", "fnr": "093450b",
  "name": "Muster Handels GmbH",      // company name from sucheFirma/bulk (lean catalog convenience)
  "rechtsform": "GES",                // legal-form code from sucheFirma/bulk, e.g. GES (GmbH), AKT (AG)
  "status": "active",                 // active | historical | deleted   (from register)
  "discovered_at": "2026-06-16T...Z", "source": "hvd_bulk" | "veraenderungenFirma" | "sucheFirma_sweep",
  "last_seen_in_registry": "2026-06-16T18:02:34Z",   // full ISO-8601 Z; bumped by every reconciliation
  "known_filings": [ { "stichtag": "2025-12-31", "doc_key": "...", "content_hash": "sha256:...",
                       "format": "jab40", "downloaded": true } ],
  "last_filing_check_at": "2026-06-16T...Z",
  "pipeline_state": "clean",          // clean | dirty | failed
  "data_version": 7,
  "dead_letter": null                 // last error if a stage failed for this company
}
```

**The registry is kept up to date in two distinct ways (this is "how often / when / where" it updates):**

| Update | What changes | Cadence / when | Done by |
|---|---|---|---|
| **Per-company state** | `known_filings` + hashes, `pipeline_state` (dirty/clean), `data_version`, `last_filing_check_at`; insert brand-new FNRs seen in the change feed | **every day** (inside the daily Operation run) | `daily` |
| **Membership / authoritative reconciliation** | add any missing FNRs, refresh `last_seen_in_registry`, mark vanished FNRs `deleted`, **emit the drift report** | **quarterly** (1st of Jan/Apr/Jul/Oct, 02:00 UTC) | `sync-registry` |

**Scheduled jobs (Container Apps Jobs, all singletons under the run lock):**
- **`job-firmenbuch-daily`** — `--mode=daily`, cron `0 3 * * *` (daily 03:00 UTC), 4 h replica timeout. The cheap steady-state delta off the change feeds.
- **`job-firmenbuch-pipeline`** — `--mode=sync-registry`, cron `0 2 1 */3 *` (1st of Jan/Apr/Jul/Oct, 02:00 UTC — quarterly), **7-day** replica timeout (`604800`s; the full prefix-walk is a many-hour-to-multi-day grind, so generous headroom guarantees one pass completes and it can never be guillotined mid-walk — the persistent checkpoint also lets it resume if ever killed). The quarterly completeness safety net.
- **`job-firmenbuch-backfill-ingest`** — `--mode=backfill-ingest`, cron `0 * * * *` (hourly), 7-day replica timeout. The one-time document backfill that **starts itself once the registry grind completes** and then idles. It is **self-deferring**: each hourly firing first checks `registry_walk_complete(blob)` (a `90-raw/_checkpoints/registry_walk_complete.json` marker written by `sync_registry`, or the walk checkpoint having been cleared) and **exits 0 if the grind is not done yet** — so it can be scheduled up-front and needs no manual hand-off. Once it has run to completion it is a no-op (the ingest checkpoint marks all companies done). See Phase I-1 for its active-only / XML-only / resumable behaviour.

**Resumable grind (persistent checkpoint).** The prefix-walk persists its frontier/done-set/incomplete-list/counts to a single JSON blob (`90-raw/_checkpoints/sync_registry_walk.json`) periodically. If the job is killed or crashes mid-grind, the next run **resumes** instead of restarting; a **completed** walk **clears** the checkpoint so the next quarterly run re-walks fresh. The persisted state holds only the *keys* of companies already seen (rebuilt as placeholders on load) — never the full payloads, which are already streamed into `99_registry` — and that placeholder set keeps the mark-vanished reconcile safe across a resume (it can never mark a pre-resume company `deleted`).

**Drift report — "what the change feed missed."** Each quarterly reconcile writes a JSON report to `90-raw/_reports/sync-registry/{run_id}.json` (and logs a summary): the companies the full sweep had to **newly add** (`seeded_companies` — a `Neueintragung` the daily feed missed) or **newly mark deleted** (`deleted_companies` — a missed `Löschung`). On the **initial seed** the registry is empty, so `was_initial_seed` is set and the per-company seeded list is omitted (it would be the entire universe, not drift). On later reconciles, a non-empty report is exactly the daily change feed's blind spots.

**Seed and reconcile are the same operation** — `sync_registry()` is an **idempotent upsert/diff** of the registry against the authoritative `data.gv.at` bulk dataset (fallback: partitioned `sucheFirma` sweep). On an **empty** registry its first run is the full **seed**; every run after is a **reconcile**. One piece of logic, not two.

**Streaming writes (not bulk-at-end).** The `sucheFirma` walk **upserts each company into `99_registry` as it is discovered** (the walk takes an `on_found` sink), so a multi-hour run is **durable** (a crash keeps everything persisted so far; an idempotent re-run self-heals), **observable** (the registry fills live — the count climbs during the run), and avoids one giant end-of-run write burst. The **mark-vanished** reconciliation (setting disappeared FNRs `deleted`) runs **only at the end over the full seen-set, and only on a COMPLETE walk** — a crash never returns from the walk, so nothing is ever falsely deleted. Each company carries `name`, `rechtsform` (legal-form code) + full-timestamp `last_seen_in_registry`.

So "every single company" is guaranteed by: *first `sync-registry` run seeds the full authoritative list → the daily run adds new registrations and updates state → later `sync-registry` runs reconcile against the authoritative dataset.* All of it lives in `99_registry`; the external bulk dataset is the authoritative source, our registry is the maintained mirror.

**Lifecycle status (active vs. gelöscht) — `99_registry` is the single source of truth.** We keep **both active and inactive companies** (`status ∈ {active, historical, deleted}`; `deleted` = gelöscht/aufgelöst, `historical` = historisch). Status is owned by the registry and updated in exactly the two registry-update paths above:
- **`sync-registry`** sets status authoritatively from the bulk dataset (and marks FNRs that disappear / whose `last_seen_in_registry` goes stale as `deleted`).
- **`daily`** picks up status flips mid-week from `veraenderungenFirma` (e.g. a Löschung), if the change feed works.

**Important:** a status change usually arrives **without a new Jahresabschluss**, so it must independently **mark the company `dirty`** (with `dirty_reason="status_change"`). The daily run then re-runs only the cheap **`present`** step for that company, refreshing the denormalized `status` in `10_presentation` — no re-download, no re-derive. `10_presentation` carries a copy of `status` purely so the MCP can filter on it; the registry remains the source of truth.

---

### 15a.1 INITIAL LOAD (one-off bootstrap — run once, by hand)

Three ordered phases, each a distinct `--mode`. You run them in sequence; each is resumable (idempotent on the registry + content hashes).

**Phase I-0 — Seed the registry** (`--mode sync-registry`, first run)
- This is the **same `sync_registry` operation** used later for reconciliation; on the empty registry it acts as the full seed. **Preferred:** the `data.gv.at` HVD **bulk dataset** — pass a `BulkSource` (`fbl_ingest.bulk`) and `sync_registry` upserts one `99_registry` doc per FNR (`source=hvd_bulk`). The bulk file is the only true completeness guarantee. *As of the 2026-06-16 probe a downloadable full-FNR bulk file could not be confirmed on the public portal (see `docs/API_PROBE_FINDINGS.md`), so the prefix-walk is the operational seed and bulk drops in via the hook once a file/URL is available.*
- **Fallback = the hardened `sucheFirma` prefix-walk** (`90_ingest/enumerate.py`): iterative, **resumable** (a `Checkpoint` persists the frontier + `done` + `seen` sets so a crashed multi-hour sweep resumes); any prefix at the **1000 cap** (live-confirmed, §16) is split deeper until every leaf is under it; dedupe by FNR. Hardening:
  - **`EXAKTESUCHE=true`** (phonetic search collapses repeated letters → infinite recursion);
  - **`MAX_PREFIX_DEPTH = 20`** (depth 6 truncates dense Austrian prefixes like "immobilien"/"betriebs" that stay over the cap for many characters);
  - an **exhaustive split alphabet** — lowercase a–z, 0–9, `äöüß`, other accented Latin (`á à â é è ê í ì î ó ò ô ú ù û ñ ç`), and ` - . & : , ' + / ( )` — **UNIONed with the characters actually observed** at each split point (so an exotic char beyond the static set is still followed); input treated case-insensitively; a **space guard** forbidding a **leading** space (no prefix starts with `" "`) and a **double** space (no `"x "` → `"x  "`). *(Learning, 2026-06-19: the API ignores leading spaces, so a `" *"` branch re-walked the whole name-space already covered by `"*"` — pure waste; no Austrian Firmenwortlaut starts with a space. `*` is only the trailing wildcard, never a prefix char.)*
  - **loud on incompleteness** — a branch still at the cap at the depth ceiling is logged as an **ERROR** and recorded in `incomplete[]` (never a silent keep-first-1000);
  - **`SUCHBEREICH=1`** (include gelöscht/historisch) and sweep **per RECHTSFORM** — the API **rejects** a `"*"`/under-3-char search unless it carries a `RECHTSFORM` **or** `ORTNR` (so a single form-less root sweep is impossible). **Verified codes (2026-06-19 via the `diag` probe — the AI-generated `docs/reference` was WRONG):** `GES, AG, KG, OG, KEG, OHG, GEN, PST, SE, EU, SPA, VER` (GmbH, AG, KG, OG, Kommanditerwerbsges., OHG, Genossenschaft, Privatstiftung, SE, Einzelunternehmer, Sparkasse, Versicherungsverein). The reference's `AKT`/`EGE` return **0** — real AG = `AG`, Genossenschaft = `GEN`. `--mode diag` re-probes RECHTSFORM/ORTNR result counts on demand.
  - a **completeness self-check** after the sweep logs per-Rechtsform counts and every depth-ceiling branch.
- **Result:** the full ~200k universe exists in `99_registry`. **Time:** minutes–hours (one bulk file, or the prefix-walk).

**Phase I-1 — Download all raw → `90-raw`** (`--mode backfill-ingest`)
- Iterate the registry; for each FNR: `sucheUrkunde` → `urkunde` (download each Jahresabschluss) → write immutably to `90-raw/{fnr}/{stichtag}/...` + `_manifest.json`; record `known_filings` + hashes in the registry. Skip filings whose `doc_key`/hash is already present (resumable at the filing level).
- **Active-only by default (best practice).** The job targets `registry.active_fnrs()` — only `status=="active"` companies — not the full universe (which includes ~45% deleted/historical FNRs). The whole-universe variant remains available (`all_fnrs()`), but the live backfill is active-only to cut API volume and storage roughly in half and prioritise the data that matters.
- **XML-only by default (best practice / cost).** `run_ingest(include_pdf=False)` filters the PDF siblings out **before download** (`UrkundeRef.is_xml`), so they cost neither an API call nor blob storage. The structured XML is everything `parse` needs; the official PDF can always be linked on demand from JustizOnline. (XML ≈ tens of KB; PDFs ≈ 1–2 MB each, ~50× the storage across the universe.) `include_pdf=True` restores the full §5.1 archive when wanted.
- **Resumable at the company level (persistent checkpoint).** `BlobIngestCheckpoint` persists the set of fully-completed FNRs to `90-raw/_checkpoints/backfill_ingest.json` every `save_every` (200) companies. A killed/timed-out replica **resumes** from the next company instead of re-querying every already-done company against the rate-limited API (a crash costs ≤ `save_every` companies of progress, i.e. minutes — not days). The run also calls the run-lock **heartbeat** between companies so a multi-day backfill never outlives its lease, and stops cleanly if the lock is lost.
- If the bulk dataset already contains the documents, ingest from it instead of per-filing API calls.
- **Parallelism (`INGEST_WORKERS`, env-overridable):** `run_ingest(workers=N)` fans the per-company work across a **thread pool** — the bottleneck is **per-request latency** (~2 s round-trips), so sequential is only ~10–11 companies/min (≈ 3 weeks for the active set!) while N concurrent workers give ~linear speed-up. The HVD API has **no documented req/s limit** (only the 1000-*results* cap, §16); the client already **retries 429/5xx with exponential backoff**, so it self-throttles — N≈8–12 is safe (~2–3 days), beyond ~16 risks sustained throttling. Thread-safety: each company is a distinct FNR → distinct Cosmos partition + distinct blob paths (no write collisions); the `httpx` client is thread-safe; checkpoint/done-set updates run on the main thread between batches. **`capture_raw` must be OFF** for the parallel backfill (the one shared `_raw` buffer can't attribute interleaved responses) — the orchestrator builds the backfill source with `capture_raw=False`, so the verbatim `_responses/` envelopes (§5.1 belt-and-suspenders) are skipped for the bulk pass; the actual filings + manifests + master `auszug` are still archived.
- **Resilience:** any per-company error (API, blob, parse) is **dead-lettered and skipped**, never crashing the multi-day run; `put_raw` is **idempotent** (`overwrite=True`, content-keyed paths) so a resumed run never dies on an already-written blob; the checkpoint is saved every `save_every` completions → fully resumable.
- **Time:** active-only (~340k companies; mostly EU/KG/OG that *don't* file → a quick check, GmbH/AG actually download) → sequential ≈ **3 weeks**; at **N=12 ≈ ~2 days**. Only Blob (and the registry's `known_filings`) is written here.

> **Never-stuck guarantees (learned the hard way — 2026-06-22).** The first full backfill ran ~2 days, then the last ~0.9% (≈3,000 companies) **stalled at ~4 companies/hour**, projecting ~a month with a permanent plateau. Root-caused to three things; all are now fixed in code so a recurring run (cron `0 * * * *`, ≥4×/week) **cannot stall**:
> 1. **Bare change-feed stubs poisoned the worklist.** ~2,300 of the tail were FNRs the delta feed (`veraenderungenFirma`) flagged with **no master data** (`name=null`). Calling the API for them resolved slowly/never. **Fix:** the backfill worklist is now `Registry.ingestable_active_fnrs()` — active companies that have a `name`; nameless stubs are **excluded** and left to the daily pipeline (which enriches them by FNR). `active_fnrs()` (all active, incl. stubs) still exists for other uses.
> 2. **A single slow FNR stalled a whole batch, and a mid-batch kill lost the batch.** The parallel path used `ex.map` over fixed batches: the batch only advanced when its **slowest** member finished, the checkpoint only saved **per batch**, and submitting a future per pending FNR risked OOM on a fresh (hundreds-of-thousands) run. **Fix:** a **bounded sliding window** (`~workers×4` in flight) with `as_completed` semantics — fast companies finish and are replaced immediately (no head-of-line blocking), the checkpoint saves **per `save_every` completions** (a kill loses ≤`save_every`, not a batch), and memory is capped regardless of worklist size.
> 3. **No per-call or per-run time bound.** One unresponsive FNR could burn ~5 min (5×60 s retries), and a run had no clean end before the platform could evict it. **Fix:** the backfill client uses a **tight timeout + few retries** (`timeout=20s, max_retries=2` → bad call fails in ~60 s, not ~5 min; the registry walk keeps the generous defaults), and `run_ingest(max_seconds=…)` (env `INGEST_MAX_MINUTES`, default 50) makes each run **end cleanly with a saved checkpoint** before eviction — the next cron run resumes the remainder. **The rule: every scheduled run is bounded and resumable, so progress is monotonic and a run can never hang.**

**Phase I-2 — Backfill all layers → Cosmos** (`--mode backfill-process`)
- Over the **whole** registry: `parse` (all) → `consolidate` (all) → build the global `CohortStats` → `derive` (all) → `present` (all). The percentile pass in `derive` requires all consolidated docs first.
- CPU-bound and fast per company; parallel by FNR range.
- **Time:** prototype did ~33,500 consolidations in **~70 min on 8 workers** → ~200k ≈ **a few hours** end-to-end (fewer with more workers).
- **Result:** `10_presentation` fully populated → **MCP goes live**.

**Total Initial Load:** dominated by Phase I-1. **Best case hours (bulk download); realistic ~1–3 days if API-crawled.**

---

### 15a.2 OPERATION (daily steady state — scheduled, runs forever)

**One job, once per day** (`--mode daily`, cron e.g. `0 3 * * *` Europe/Vienna). A single run does the whole chain **sequentially** for only the companies that changed — it does **not** re-touch all 200k.

Sequence inside one daily run:
1. **Acquire the run lock** (§15a.3). If already held, **exit immediately** (no overlap).
2. **Detect changes since the watermark:**
   - *If change feeds work (§16):* `veraenderungenUrkunden(watermark→today)` → new/changed filings; `veraenderungenFirma(watermark→today)` → new registrations (`Neueintragung`), **status changes (Löschung → `deleted`)**, and other register changes. Update `status` in `99_registry`, mark those FNRs `dirty` (with `dirty_reason`), and insert brand-new FNRs. A **status change alone** is enough to mark a company dirty (re-`present` only).
   - *If they don't:* **rolling re-scan** — recheck 1/Nth of the registry per day via `sucheUrkunde` (so every company is rechecked every N days), diffing against `known_filings`.
3. **Ingest** the new raw documents for the dirty FNRs → `90-raw`.
4. **Process the dirty set sequentially:** `parse → consolidate → derive → present` for those FNRs only. Recompute `CohortStats` once per run (cheap; mostly unchanged universe).
5. **Advance the watermark** to `today` **only if the run fully succeeded**; mark processed FNRs `clean`. Release the lock.
- **Runtime:** change-feed path = **minutes** (hundreds–low thousands of changes/day for all of Austria); rolling-rescan path = bounded (≈200k/N calls/day).
- **New companies** are handled inside this same run (step 2) — not a separate script, not "all at once."

**Registry reconciliation** (`--mode sync-registry`, **quarterly** default — `0 2 1 */3 *`): the **same operation as the seed** — re-pull the authoritative bulk dataset (fallback: `sucheFirma` grind) and upsert/diff `99_registry` to catch missed additions/deletions, refresh `last_seen_in_registry`, and **emit the drift report** (`90-raw/_reports/sync-registry/{run_id}.json`) of what the daily change feed missed. Runs as its own scheduled job (`job-firmenbuch-pipeline`, 7-day replica timeout, resumable via the persistent checkpoint), under the run lock so it can't overlap the daily job.

---

### 15a.3 Scheduling & concurrency — how runs don't overrun each other

> This answers "does each step run independently? how often — once per day? should one run trigger the next? how to stop them overrunning?"

**Execution model in one paragraph.** Each stage (`ingest`/`parse`/`consolidate`/`derive`/`present`) is an independently *runnable and testable* module — but in **Operation they are NOT separately-scheduled scripts**. There is exactly **one scheduled job per day** (`--mode daily`); inside that single job the stages run **in sequence** (`ingest → parse → consolidate → derive → present`) over the day's changed companies, then the job exits. So "once per day, one job, stages in order" — not five cron jobs racing. The only other scheduled job is the quarterly `sync-registry` reconcile. Stages run *standalone* only during the one-off Initial Load and in tests/backfills (`--mode backfill-*`), where you invoke a stage directly over the whole set.

- **Cadence: once per day**, by cron. **Runs do not trigger each other** — each daily run is independent and self-contained, coordinated only by the **watermark** in `99_registry` (a failed/short run just means the next day resumes from the un-advanced watermark). No cascade, no run chaining.
- **Stages are sequential *within* a run** (in-process: `ingest → parse → consolidate → derive → present`), so there is no inter-stage race and no separately-scheduled layer jobs racing.
- **Singleton / non-overlap:** a **run lock** (a lease doc in `99_registry` with a heartbeat/TTL) is acquired at the start of any run. If a previous run is still going (e.g. a heavy reconciliation), the new cron firing **finds the lock held and exits** — never two runs at once. Container Apps Job parallelism for the *daily* job is set to a single instance; internal worker parallelism (within the one run) is bounded by the token-bucket.
- **Mode-aware lease length (never-stuck — learned 2026-06-22):** a killed replica can't run its release `finally`, so a held lease lingers for its whole TTL and **wedges every later cron firing** (all defer). With the old fixed 4 h TTL, one OOM'd ingest run blocked the hourly schedule for 4 hours. Fix: the **recurring jobs (`backfill-ingest`/`backfill-process`/`daily`) use a short 30-min lease** — they heartbeat between companies, so a live run renews it indefinitely, while a *dead* run's lock self-frees within ~30 min and the next cron firing picks up cleanly. The **quarterly `sync-registry` walk keeps the 4 h lease** because it does NOT heartbeat (a multi-hour walk must not let its own lease expire and be overtaken). So: short self-healing lease where there's a heartbeat, long lease where there isn't.
- **Backfill before enable:** the daily cron is **disabled during Initial Load** and switched on only after Phase I-2 completes — so the one-off bootstrap and the steady-state job can never collide.
- **Idempotent throughout:** content hashes + watermark mean re-running a day, or recovering from a mid-run failure, produces the same result and never double-counts.

---

### 15a.4 Modes summary (single entrypoint)
`orchestration --mode {sync-registry | backfill-ingest | backfill-process | daily}`.
- **Initial Load (one-off, by hand, in order):** `sync-registry` (first run = seed) → `backfill-ingest` → `backfill-process`.
- **Operation (scheduled):** `daily` (the steady-state job, `0 3 * * *`) + `sync-registry` (quarterly registry reconciliation, `0 2 1 */3 *` — same code as the seed, emits the drift report).

Same image, same code; only the mode and schedule differ, and the run lock guarantees no two runs ever overlap.

---

## 15b. Edge cases & known data gotchas (mined from any external project or prior codebase)

These are **real**, observed in the prototype's code/data — each must be handled or the pipeline will silently corrupt data or miss companies. Build tests for each.

**Parsing / formats**
1. **Three XML variants, not two** — `legacy_finanzonline` (`HGB_*` + `POSTENZEILE/BETRAG`, `GJ`), `firmenbuch_2025` (`HGB_*` + `BETRAG_GJ`, `GESCHAEFTSJAHR`), and `jab40_semantic` (`BILANZ_*`/`UMSATZERLOESE` element names). Detect per filing; map all to canonical (§8.4).
2. **The prototype's parser only handles `HGB_*`-style tags** — a truly **semantic JAb 4.0** filing would currently be *missed* (positions empty). Confirm what the 2026-mandatory format emits and ensure `jab40_semantic` extraction exists. **This is the biggest correctness risk for new filings.**
3. **`XXX_*` and unknown position codes** appear (e.g. `XXX_224_3_D_X` = hybride Finanzinstrumente, `XXX_224_3_B_X` = Investitionszuschüsse). Keep a passthrough for unrecognized codes — never silently drop.
4. **`WERT_TSD = j` → values in thousands** (×1000). Missing this inflates everything 1000×.
5. **Multi-line company names** (`F_NAME/Z`, `FIRMENWORTLAUT/ZEILE`) with hyphen-gluing (segment ending in `-` joins without a space).
6. **Source label typos are authoritative** (Justiz's own spec: "Kaßenbestand", "außtehende", "Erzeugniße", "Jahresüberschuß"). Map by code/element, not by label text.
7. **Parse errors must dead-letter, not crash** — emit a stub doc with an `error` field (prototype pattern).
8. **`BETRAG_VJ` (prior-year column) is deliberately NOT used as the value of record** — each year's own filing is authoritative (avoids double-source conflicts). Use `BETRAG_VJ` only as an optional reconciliation cross-check.

**Financials / semantics**
9. **~96.7% of companies file Bilanz only, no GuV** (Klein/Kleinst, §242 UGB verkürzte Bilanz). So `has_guv`/`has_guv_latest` is mostly false; revenue/EBIT/margins are absent for most → ratios must degrade gracefully (null, not error).
10. **Negative Eigenkapital is legal and real** (`HGB_224_3_A < 0`) → equity ratio can be negative; flag `negative_equity`, don't treat as a parse bug.
11. **Rumpfwirtschaftsjahr / fiscal-year changes** — a company can file a short/shifted year (two filings mapping to one calendar year, or a gap). Consolidate by **fiscal year from the filing**, dedupe resubmissions per Stichtag, and tolerate gaps in the history.
12. **GuV present in some years only** — store per-year; the rollups (`has_guv_latest`, `guv_years`) exist precisely for this (§11/§12).

**People / age feature**
13. **Birth-date coverage is PARTIAL** — not every signatory record has `GEB_DAT`; coverage varies by year. So `age_at_signing`/`birth_year` will be null for many companies — set expectations, don't assume presence.
14. **Malformed `GEB_DAT`** values exist (not all `YYYY-MM-DD`) → parse defensively, null on failure.
15. **`PERS_KENN` (role code) may sit in a sibling list, not inside `UNTER`** → positional-index fallback (prototype handles this).
16. **A second birth-date source exists** in some schemas (`PERSON/GEBURTSDATUM` vs `UNTER/GEB_DAT`) — check both.

**Enumeration / universe**
17. **`sucheFirma` has a result cap of exactly 1000** (live-confirmed) and no native paging → must prefix-walk/partition (§15a.1).
18. **`EXAKTESUCHE=true` is mandatory** for prefix-walking; phonetic mode infinite-loops.
19. **Prefix-walk can leave dense branches incomplete** at the depth ceiling → mitigated with `MAX_PREFIX_DEPTH=20` + an exhaustive, observed-char-augmented split alphabet + a loud completeness self-check; the bulk dataset remains the completeness-safe seed and is preferred when available (prefix-walk is the resumable fallback). See §15a.1.
20. **Active vs inactive scope** — `SUCHBEREICH=4` returns active only; use `SUCHBEREICH=1` to include gelöscht/historisch (you want both). And sweep **all Rechtsformen**, not just `GES`, for "every company."
20a. **The pipeline + MCP are LEGAL-FORM-AGNOSTIC — NOT GmbH-only.** This is a common misconception worth stating explicitly: nothing in `parse → consolidate → derive → present → mcp_server` branches on legal form. A **Jahresabschluss is filed under the same UGB §224 (Bilanz) / §231 (GuV) schema regardless of Rechtsform** — a GmbH, AG, KG, OG, Genossenschaft, Privatstiftung or SE all produce the same XML element/code structure, which the parser maps to the same 317-position canonical taxonomy. `legal_form` is carried as a **master-data attribute** (a filter/facet), never as a parsing switch. So **no per-form adaptation of the pipeline or MCP is needed** to cover the other Rechtsformen — the same code already handles every form that files a standard UGB Jahresabschluss. (Coverage differences seen in `backfill-status` are about *who files how much* — most EU/KG/OG are non-filers or file rarely — not about the code's ability to parse a given form.)
20b. **Banks (`SPA`) and insurers (`VER`) are OUT OF SCOPE by construction — verified, not assumed.** A live investigation (June 2026, see **`docs/Rechtsform_Coverage.md`**) found: of 21 active Sparkassen/Kreditinstitute, **0 filed** any Jahresabschluss in the pipeline; of 10 Versicherungsvereine, the 4 "filers" have only a pre-2013 *skeleton* XML (no position rows) or PDF-only — **none has a modern position-bearing XML**. Corroborating schema evidence: the **JAb 4.0 document-type enum is `JAB`/`JAB-ANLAGE12`/`JAB-ANLAGE32`/`KAB` only — there is no bank or insurance document type**. Banks report under the **BWG** (OeNB/FMA) and insurers under the **VAG** (FMA), on their own Formblätter, *not* via the Firmenbuch JAb. So there is **no UGB data to parse** for them (it's not a "lands-in-passthrough" case — the data simply isn't here). Covering them would be a **separate ingestion + taxonomy project** (new source + `core/mapping/` entries + form-aware parsing), explicitly **post-v1** and low value given the counts.
20c. **Empirically confirmed per-form coverage** (real samples run end-to-end through `parse→consolidate→derive→present`, see `docs/Rechtsform_Coverage.md`): **GES, AG, KG, OG, GEN, SE, EU, PST all parse into full financials** with a modern XML (e.g. AG `064499b` 2025 = 137 tags, GEN `093299f` 2025 = 119 tags, KG €5.95 M Bilanzsumme, OG/SE with GuV). The only reason a given company yields empty financials is **data vintage/medium** — PDF-only or pre-~2013 skeleton XML carry no `POSTENZEILE`/`BETRAG` rows — and that is **identical across all forms, including GmbH** (not a per-form defect). To surface any standard form, just add its code to the `backfill-process` worklist via `PROCESS_RECHTSFORMEN` — no code change. Recommended order after GES: `KG` (12k filers), `AG`, `OG`, `GEN`, then `SE`/`EU`/`PST`.

**Parsing / data (live-validated against 100 random companies)**
21. **Free-text position slots carry real money.** Filers use non-`HGB_` tags `FREI`/`FREI1`/`FREI3`/`FREIER_SUB_POSTEN` (and the admin field `GEB_BEFREIUNG`) for positions outside the fixed taxonomy — sometimes very material (e.g. €71M "Gewinnvortrag aus Vorjahren"). The extractor must capture these in passthrough (keyed `CODE: <TEXT>`, collision-safe) — never drop them (§5.1, §8.4).
22. **Negative Bilanzsumme / zero Anlagevermögen is real** → ratio denominators must be strictly positive. `anlagedeckungsgrad_1` skips any year where `bilanzsumme ≤ 0` or `anlagevermoegen = 0` (Appendix C.2); never divide by zero.

**Documents**
21. **Image-only PDF Anhang/Lagebericht** can't be text-extracted (no OCR in v1) — consistent with the PDF-defer; the document is still stored and linked.

---

## 16. Open items & assumptions

Each item is an **assumption the spec already designs around** — where it matters, **both branches are fully specified** so the build is never blocked. Several were **live-confirmed on 2026-06-16** (see `docs/API_PROBE_FINDINGS.md`) — marked ✅ below.

1. ✅ **Tier capabilities (confirmed).** Auth is the **`X-API-KEY` header** (not WS-Security). **`auszug` works** on this tier and returns rich master data (name, address, Geschäftszweig, persons with birth dates). **The change feeds work** → the **`change_feed`** delta branch is active. A broad `sucheFirma` **caps at exactly 1000**. (The rolling-rescan branch in §15a remains available but is not the active path.)
2. ⚠️ **Enumeration source (partly confirmed).** The HVD dataset exists on data.gv.at, but a downloadable **full-FNR bulk file** could not be confirmed on the public portal. → The hardened **`sucheFirma` prefix-walk** is the operational seed (§15a.1); the bulk dataset is **preferred** and drops in via the `BulkSource` hook the moment a file/URL is available.
3. **JAb 4.0 leaf mapping** — finalize element paths in `jab40_map.py` from `JAb_4_00-Uebermittlung.xsd` + `4.00_Struktur_JAb_*.xlsx` and one real JAb 4.0 filing.
4. **Employees element** in JAb 4.0 (legacy is `HGB_Form_3_16/ANZAHL`).
5. **HVD API rate-limit / fair-use** ceiling for the full-universe sweep.
6. **GDPR lawful-basis** before flipping `EXPOSE_PERSONAL_DATA`. → **RESOLVED 2026-06-24** (owner): officer names are public Firmenbuch data, served for per-query lookup; flag set TRUE in prod; birth data year-only; bulk extraction barred in the terms (§8.7).
7. ✅ **`auszug` field paths (confirmed).** `location` (`FI_DKZ03`), Geschäftszweig (`FI_DKZ05`), Sitz/Rechtsform, persons (`PER/PE_DKZ02`), registry events (`VOLLZ`) — mapped in `firmenbuch_client`.

> None of these block building: the architecture, schemas, and runbook branches are all stable in this document.

---

## Appendix C — Canonical positions & ratio formulas (authoritative, from the prototype)

Extracted from `pipeline/shared/position_mapping.py` (the canonical taxonomy, ~2,647 entries) and `scripts/phase4_metrics.py` (the metric math). The position mapping table maps **both** the old `HGB_*`/`XXX_*` codes **and** the new `v4_elements` to one canonical snake_case name — copy it wholesale into `core/mapping/`.

### C.1 Core canonical positions used by the metrics

| Canonical name | German | `HGB_*` code (legacy) | `v4_elements` (semantic) | Section |
|---|---|---|---|---|
| `aktiva` (= Bilanzsumme) | Aktiva | `HGB_224_2` | `AKTIVA` | bilanz |
| `anlagevermoegen` | Anlagevermögen | `HGB_224_2_A` | `ANLAGEVERMOEGEN` | bilanz |
| `umlaufvermoegen` | Umlaufvermögen | `HGB_224_2_B` | `UMLAUFVERMOEGEN` | bilanz |
| `eigenkapital` | Eigenkapital | `HGB_224_3_A` | `EIGENKAPITAL` | bilanz |
| `rueckstellungen` | Rückstellungen | `HGB_224_3_C` | `RUECKSTELLUNGEN` | bilanz |
| `verbindlichkeiten` | Verbindlichkeiten | `HGB_224_3_D` | `VERBINDLICHKEITEN` | bilanz |
| `umsatzerloese` | Umsatzerlöse (§231) | `HGB_231_*` | `UMSATZERLOESE` | guv |
| `rohergebnis` | Rohergebnis (§279 alt.) | `HGB_231_*` | `ROHERGEBNIS` | guv |
| `materialaufwand` | Materialaufwand | `HGB_231_2_5*` | `MATERIALAUFWAND` | guv |
| `personalaufwand` | Personalaufwand | `HGB_231_2_6*` | `PERSONALAUFWAND` | guv |
| `abschreibungen` | Abschreibungen | `HGB_231_2_7` | `ABSCHREIBUNGEN` | guv |
| `zwischensumme_betriebserfolg` (= EBIT) | Betriebserfolg | `HGB_231_*` | `ZWISCHENSUMME_BETRIEBSERFOLG` | guv |
| `jahresueberschuss_jahresfehlbetrag` | Jahresüberschuss | `HGB_231_*` | `JAHRESUEBERSCHUSS_JAHRESFEHLBETRAG` | guv |
| `durchschnittliche_anzahl_arbeitnehmer` | Ø Arbeitnehmer | `HGB_Form_3_16` | `DURCHSCHNITTLICHE_ANZAHL_ARBEITNEHMER` | anlage_2 |

> **`umsatzerloese` and `rohergebnis` are DISTINCT and must never be conflated** (§231 full disclosure vs §279 alternative where the company hides Umsatz+Material and publishes only their difference). `revenue_basis` records which one a company used.

### C.2 Ratio formulas (with the prototype's meaningfulness caps)
- `equity_ratio = eigenkapital / aktiva`
- `debt_ratio = (rueckstellungen + verbindlichkeiten) / aktiva`
- `debt_to_equity = verbindlichkeiten / eigenkapital` — only if `eigenkapital > 0`; **null if > 50** (noise)
- `working_capital_ratio = umlaufvermoegen / verbindlichkeiten` — only if `verbindlichkeiten > 0`; **null if > 20**
- `anlagedeckungsgrad_1 = eigenkapital / anlagevermoegen` — only if `aktiva > 0` **and** `anlagevermoegen ≥ 5%` of `aktiva`; **null if > 20**. (The `aktiva > 0` guard is required: a negative Bilanzsumme makes the 5% floor negative and would otherwise admit `anlagevermoegen = 0` → divide-by-zero, §15b-22.)
- `ebit = zwischensumme_betriebserfolg`; `ebitda = ebit − abschreibungen` (Abschreibungen stored **negative**, so this adds it back)
- `ebit_margin = ebit / umsatzerloese`; `ebitda_margin = ebitda / umsatzerloese`; `net_margin = jahresueberschuss / umsatzerloese` — **only on `umsatzerloese`, never `rohergebnis`**; null if no Umsatz
- `personalkostenquote = |personalaufwand| / umsatzerloese`; `materialaufwandsquote = |materialaufwand| / umsatzerloese`
- `roa = jahresueberschuss / aktiva`; `roe = jahresueberschuss / eigenkapital` (only if `eigenkapital > 0`)

### C.3 Growth, profiles, size
- `cagr(start,end,n) = (end/start)**(1/n) − 1` → **null when start ≤ 0 or end ≤ 0** (so negative-equity series yield no CAGR). `growth_1y = (end−start)/start`, null if start ≤ 0. If the exact start year is missing, use the **closest** available year and its actual span.
- `growth_profile` (priority `umsatz_3y_cagr` → `rohergebnis_3y_cagr` → `bilanzsumme_3y_cagr`): `< −0.05` shrinking · `< 0.03` stable · `< 0.15` growing · else fast_growing.
- `capital_profile`: equity_ratio `< 0.15` over_leveraged · `< 0.60` balanced · else over_capitalized.
- **Size class `gkl` is W/K/M/G** (W = Mikro/Kleinst, K = Klein, M = Mittel, G = Groß) — the UGB §221 Größenklasse **from the filing**. SEPARATELY, `size.bilanzsumme_band` is a derived bucket purely by Bilanzsumme thresholds: `≥100M` very_large · `≥25M` large · `≥6.25M` medium · `≥450k` small · else micro. **These are different axes** — a filer self-classified `gkl="K"` (a 2-of-3-criteria call) can carry `bilanzsumme_band="medium"` when its Bilanzsumme alone exceeds €6.25M; not a contradiction. (Renamed from `band` for exactly this reason.)
- **Peer percentiles** are **size-band-relative** (rank within the same `gkl`), computed in a second pass over the universe, for `bilanzsumme`, `equity_ratio`, `bilanzsumme_5y_cagr`, `eigenkapital_5y_cagr`.

> All of the above is deterministic, no-LLM, and already proven on ~33k real companies. Port it into `derive` and unit-test against the prototype's numbers.

---

## Appendix index & companion files

The spec ships as a small set of files so the coding agent has machine-readable references, not just prose. Place the companion files in `docs/` (or `core/mapping/` where noted).

| Appendix | Where | Contents |
|---|---|---|
| **A — Legacy FinanzOnline mapping** | inline (this doc / §8.4) | `HGB_*`/`XXX_*` codes → canonical |
| **B — JAb 4.0 mapping** | inline (this doc / §8.4) | semantic `v4_elements` → canonical |
| **C — Canonical positions & ratio formulas** | inline (this doc) | core position set + exact ratio formulas, caps, thresholds |
| **D — Full canonical position taxonomy** | **`appendix_position_mapping.json`** (companion file → `core/mapping/`) | **all 317 canonical positions**, each with `label_de`, `category`, `hgb_codes`, `v4_elements`. The authoritative lookup table — copy in verbatim, don't hand-type. It is the **single source** for code ↔ canonical ↔ §-label: `paragraph_ref(code)` derives the human UGB reference from the code structure (`HGB_224_2_A_II` → `§224 Abs 2 A II`; `§231` GuV uses `Z`), and `paragraph_ref_for_canonical(name)` resolves it from the canonical's primary HGB code so a position parsed from a JAb 4.0 element still carries the official §-ref. Every served line item (`get_company_details`, `get_company_history`) exposes its `source_codes` + `paragraph_ref` (Part A). |
| **E — Per-stage file formats + golden samples** | **`pipeline-step-samples.md`** (companion file) | the **defined file format for every pipeline stage** with a real chained example (FNR `093450b`) |
| **R — Reuse boundary (shared vs product)** | inline (this doc, below) | per-package `1:1 shared` / `adapt` / `product-local` classification for the `agentic-first` monorepo split |
| **Fixtures** | the two real XML files already provided | parser test fixtures (legacy + firmenbuch_2025) |

### Is there a defined file format for each pipeline step? — **Yes.**
Every stage has a **typed contract** (the Pydantic models in §6) **and** a **golden sample document** in Appendix E (`pipeline-step-samples.md`), one per stage:

| Stage | Store / format | Schema (this doc) | Sample (Appendix E) |
|---|---|---|---|
| `90_raw` | Blob: original `.xml`/`.pdf` + `_manifest.json` | raw `Meta` + artifact manifest | Stage 0 sample |
| `70_parsed` | Blob: one JSON per filing | `ParsedFiling` (§6) | Stage 1 sample |
| `50_consolidated` | Cosmos doc per FNR | `ConsolidatedCompany` (§6) | Stage 2 sample |
| `30_derived` | Cosmos doc per FNR | `DerivedCompany` (§6) | Stage 3 sample |
| `10_presentation` | Cosmos doc per FNR (served) | presented model + `provenance` | Stage 4 sample |
| `99_registry` | Cosmos doc per FNR | registry doc (§15a.0) | — |

So the agent never guesses a stage's shape: it reads the model (§6), the sample (Appendix E), and — for line items — the full taxonomy (Appendix D).

---

## Appendix R — Reuse boundary (shared vs product)

The classification behind the `agentic-first` monorepo split (§3, §3.5). "Reuse" is judged by
whether the code carries **Firmenbuch/UGB/ÖNACE knowledge**, not by whether its algorithm is
generic. The hard rule: dependency arrows only ever point **product → shared**.

| Package (`fbl_*`) | Location | Class | Rationale |
|---|---|---|---|
| `core` (lineage, config, logging, storage/, models/`meta`+`metric`) | `packages/core` | **1:1 shared** | pure infra + source-agnostic contracts; no Firmenbuch knowledge |
| `auth` | `packages/auth` | **1:1 shared** | signup/token/metering/OAuth over `00_accounts`; only brand strings are product-specific (parameterise via config) |
| `core_at` (`mapping`, `models/`filing·company·mcp, `classification`, `directories`, `financial_institution`, `austria`, `formats`, `esvg`) | `products/agentic-firmenbuch` | **product-local** | encodes UGB/ÖNACE/Austria; the domain models a second product would redefine |
| `firmenbuch_client` | `products/agentic-firmenbuch` | **product-local** (pattern reusable) | the `RegisterSource` Protocol is a reusable seam; the SOAP/HVD impl is Austria-only |
| `99_registry`, `90_ingest` | `products/agentic-firmenbuch` | **product-local** | AT enumeration/change-feed + registry semantics |
| `70_parse` | `products/agentic-firmenbuch` | **product-local** | UGB XML (legacy + JAb 4.0) → canonical |
| `50_consolidate` | `products/agentic-firmenbuch` | **adapt later** | merge/supersede framework is generic but operates on AT `ParsedFiling`/`ConsolidatedCompany` |
| `30_derive` | `products/agentic-firmenbuch` | **promotion-candidate** | ratio/growth/cohort math is source-agnostic but binds to AT-shaped `ConsolidatedCompany`/`DerivedCompany`; promote to `packages/` after the domain models are abstracted |
| `mcp_server` | `products/agentic-firmenbuch` | **promotion-candidate** | FastMCP app + OAuth/DCR + `McpService` framing is source-agnostic; the service layer + `CompanyCard`/`SearchFilters` are AT-shaped |
| `orchestration` | `products/agentic-firmenbuch` | **adapt later** | the `--mode` runner/runlock/loaders framework is reusable; it currently wires the AT stages |

**Why not promote `derive`/`mcp_server` now:** doing so under the "no logic change / keep the suite
green" constraint would only be possible by dragging the AT-shaped Pydantic models into `packages/core`,
which would contaminate the shared package worse than leaving the stages in the product. The clean
promotion is a **model-abstraction pass** (source-agnostic domain protocols in `packages/`, concrete
AT impls in the product) — tracked as a later V2 item, not part of the structural split.
