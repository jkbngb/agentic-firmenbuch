# TODO — Search Quality & Latency Program (P0–P4)

> **Audience:** an autonomous coding agent working in THIS repo. Everything you need is in
> this file — file paths, line anchors, schemas, acceptance criteria. Do NOT guess; if a
> stated anchor moved, `grep` for the quoted code. Work phase by phase, commit per task.
>
> **Background (measured 2026-07-12 against the live DB `cosmos-firmenbuch-xbjux2hw`,
> container `10_presentation`, 341,406 docs, serverless, Germany West Central):**
>
> | Query | Latency | RU |
> |---|---|---|
> | COUNT with `CONTAINS(LOWER(c.identity.name), …)` | 5.7–7.4 s | 4,200–6,500 |
> | Same CONTAINS on an **indexed** text path (manager_name) | 0.5 s | 334 |
> | Full name-search triple (2 COUNTs + page) | 11–16 s | ~10,600 |
> | Filter page (PLZ prefix + range + ORDER BY) | 0.19 s | 187 |
> | `00_directories` full read (**runs on EVERY search**) | 0.2 s | — |
>
> Root cause of slow name search: `/identity/name` is missing from the opt-in indexing
> policy. Everything below follows from live measurements, not speculation.

## Ground rules (read first)

1. **CI is the gate:** `ruff` + `mypy --strict` + `pytest` must pass. Run
   `uv run pytest products/agentic-firmenbuch/tests -x -q` before every commit.
2. **In-memory twin parity:** every change to the Cosmos WHERE/ORDER logic in
   `products/agentic-firmenbuch/packages/mcp_server/src/fbl_mcp_server/service/search.py`
   has an in-memory Python twin (`_matches`, `_sorted_nulls_last`, the
   `isinstance(raw_total, int)` branch). The in-memory store ignores SQL. Keep both paths
   equivalent and tested — this is an existing invariant of the codebase.
3. **Deploys are manual** (`az acr build` + `az containerapp update`, see
   `docs/` runbooks / memory). Code changes here do NOT auto-deploy. Each task lists its
   deploy step; leave live rollout to the owner unless told otherwise.
4. **⚠️ MCP tool-schema freeze:** tasks marked **[SCHEMA]** change the tool input schema
   (SearchFilters/Sort) or descriptions. An Anthropic Connectors Directory review may be
   in flight (see `docs/DIRECTORY_SUBMISSION.md`). Implement + test behind the existing
   patterns, but the OWNER decides when a [SCHEMA] task ships. Response-body additions
   (new optional fields in SearchResponse) are NOT schema changes and can ship anytime.
5. **No hardcoding / no per-query special cases.** Every mechanism below is generic for a
   whole class of queries. Keep it that way.
6. Bicep is source of truth for infra, but live application of container-level changes is
   done with explicit `az` CLI (idempotent) — update **both** (bicep for record, CLI for
   live). `infra/main.json` is compiled output; regenerate with `az bicep build` if the
   repo tracks it, otherwise leave it.

---

# Phase 0 — latency killers (no tool-schema changes)

## T1 — Index `/identity/name` (the single biggest win)

**Problem:** every name query loads up to ~90k documents (~250 MB) because the name path
is not in the opt-in index.

**Where:**
- `infra/modules/cosmos.bicep` — the `10_presentation` container resource, in the
  `includedPaths` array (search for `'/identity/status/?'`). Add:
  ```bicep
  { path: '/identity/name/?' }
  ```
- Live application (owner runs, or agent prepares the exact command):
  ```bash
  # Export current policy, add the path, apply. Container: 10_presentation,
  # account cosmos-firmenbuch-xbjux2hw, rg rg-firmenbuch-prod, db firmenbuch.
  az cosmosdb sql container show -a cosmos-firmenbuch-xbjux2hw -g rg-firmenbuch-prod \
    -d firmenbuch -n 10_presentation --query resource.indexingPolicy > /tmp/idx.json
  # edit: append {"path": "/identity/name/?"} to includedPaths
  az cosmosdb sql container update -a cosmos-firmenbuch-xbjux2hw -g rg-firmenbuch-prod \
    -d firmenbuch -n 10_presentation --idx @/tmp/idx.json
  ```
  Reindexing runs online in the background (est. one-time cost 2–5 €). No downtime.

**Acceptance:** `SELECT VALUE COUNT(1) … CONTAINS(c.identity.name, 'bau', true)` completes
< 1.5 s / < 800 RU (measure with the script from T-VERIFY below).

## T2 — Case-insensitive CONTAINS via the 3-arg form (index-friendly)

**Problem:** `CONTAINS(LOWER(x), @v)` cannot use the index and costs ~2× the RU of
`CONTAINS(x, @v, true)` (measured: 4,261 vs 2,118 RU pre-index).

**Where:** `…/mcp_server/src/fbl_mcp_server/service/search.py`, `_build_where`:
- name (`CONTAINS(LOWER(c.identity.name), @name)` — around line 111)
- manager_name (around line 146)
- geschaeftszweig (around line 206)
- city (around line 212)

Change each to `CONTAINS(<path>, @param, true)` and stop lowercasing the bound value
(keep `.lower()` OUT of the SQL param; the ci flag handles it). The Python twin in
`_matches` already lowercases both sides — unchanged. `STARTSWITH(c.location.postal_code, …)`
stays as is (digits).

**Acceptance:** existing search tests green; live RU for a name COUNT drops vs T1-only.

## T3 — Cache the financial-institution directory (−0.2 s on every search)

**Problem:** `load_fi_directory` (in
`products/agentic-firmenbuch/packages/core_at/src/fbl_core_at/directories.py`, ~line 187)
does a full `00_directories` read (487 rows) on **every** `search_companies` call
(called at `service/search.py` ~line 261) and in the card builders of other tools.

**Fix:** add a module-level TTL cache in `directories.py`:

```python
_FI_CACHE: dict[int, tuple[float, dict[str, str]]] = {}
_FI_TTL_SECONDS = 900.0

def load_fi_directory_cached(cosmos: CosmosStoreLike) -> dict[str, str]:
    """TTL-cached wrapper; keyed by store identity so tests with fresh in-memory
    stores never see stale data. Registers change daily — 15 min staleness is fine."""
    key = id(cosmos)
    now = time.monotonic()
    hit = _FI_CACHE.get(key)
    if hit and now - hit[0] < _FI_TTL_SECONDS:
        return hit[1]
    data = load_fi_directory(cosmos)
    _FI_CACHE[key] = (now, data)
    return data
```

Switch all MCP-server call sites (`grep -rn "load_fi_directory" products/…/mcp_server`)
to the cached variant. Export it from `fbl_core_at.directories`/models `__init__` as the
existing function is exported. Add a unit test (monotonic monkeypatch → second call hits
cache; new store id → miss).

## T4 — Kill the double COUNT: page-first, count in parallel, ranked-COUNT only when needed

**Problem:** `search_companies` runs sequentially: total COUNT → ranked-bucket COUNT →
page A → maybe page B (see `service/search.py::search_companies` ~line 244 and
`_cosmos_page` ~line 289). Two of these are expensive scans; all are serial.

**Target behavior (generic, exact totals kept):**

1. Fire **total COUNT** and **page query** concurrently (2 threads;
   `concurrent.futures.ThreadPoolExecutor` — the sync azure-cosmos client is thread-safe
   for queries; the `CosmosStoreLike` protocol stays sync).
2. Restructure `_cosmos_page` so the **ranked-bucket COUNT disappears for the common
   case**:
   - Run bucket-A page (`… AND IS_DEFINED(path) … ORDER BY … OFFSET start LIMIT page_size`).
   - `len(A) == page_size` → done, no count.
   - `len(A) > 0` and short → the ranked bucket has exactly `start + len(A)` rows
     → bucket-B offset is `0`; top up with `page_size - len(A)` rows. No count.
   - `len(A) == 0` and `start > 0` → only NOW issue the ranked COUNT to compute
     `b_offset = start - count_ranked` (deep-page case, rare with LLM clients).
   - `start == 0` and `len(A) == 0` → `b_offset = 0`. No count.
3. Add to `SearchResponse` (in
   `products/agentic-firmenbuch/packages/core_at/src/fbl_core_at/models/mcp.py`):
   `has_more: bool = False` (derived: `start + len(results) < total`). Response-only —
   safe to ship (ground rule 4).

Keep the in-memory branch exactly as is (it already computes everything in Python).
Update/extend the unit tests that cover the two-bucket stitching (issue #32 tests —
`grep -rn "bucket" products/agentic-firmenbuch/tests`): all boundary cases above.

**Acceptance:** for page 1, exactly **2** Cosmos queries run when the ranked page is full
(**3** when stitching), verifiable via a counting fake store in tests; live p50 of a
name search < 1 s after T1+T2+T4.

## T5 — Telemetry foundation (App Insights + RU + session round-trips)

**Problem:** no p50/p95, no RU-per-query, no rounds-per-intent measurement — improvements
can't be proven. `Settings.appinsights_connection_string` already exists
(`packages/core/src/fbl_core/config.py` line ~33) but nothing emits.

**Implement:**
1. New module `products/agentic-firmenbuch/packages/mcp_server/src/fbl_mcp_server/telemetry.py`:
   - `configure_telemetry(settings)` — if `appinsights_connection_string` is set, wire
     `azure-monitor-opentelemetry` (add to `mcp_server` package deps in its
     `pyproject.toml`; keep import lazy/optional so offline tests never need it).
   - A `contextvars.ContextVar[float]` RU accumulator + `@contextmanager tool_span(tool, ctx)`
     that emits one custom event/span per tool call with attributes:
     `tool`, `duration_ms`, `ru_total`, `result_total`, `zero_hit: bool`, `page`,
     `filters_used` (**field NAMES only, never values** — privacy), `plan`,
     `mcp_session_id` (from `ctx.request_context.request.headers.get("mcp-session-id")` —
     this is the streamable-HTTP session and IS the "LLM rounds per session" key).
2. RU capture: in `packages/core/src/fbl_core/storage/cosmos.py::CosmosStore.query`
   (line ~74), iterate `by_page()` instead of the flat iterator and after each page add
   `float(client.client_connection.last_response_headers.get("x-ms-request-charge", 0))`
   to the ContextVar (import from a tiny `fbl_core.metrics` module so `core` does not
   depend on the MCP server; default no-op when the ContextVar is unset).
3. Hook `tool_span` into every `McpService.*` method in
   `…/mcp_server/src/fbl_mcp_server/app.py` (one decorator, applied uniformly).
4. Infra: add an App Insights (workspace-based) resource to `infra/` if none exists
   (`grep -rn "microsoft.insights" infra/`), and document the manual step:
   `az containerapp update -n app-firmenbuch-mcp -g rg-firmenbuch-prod --set-env-vars APPINSIGHTS_CONNECTION_STRING=<value>`.
5. Dashboard/queries: commit `docs/telemetry/QUERIES.md` with 4 KQL queries: p50/p95 per
   tool, RU per tool, zero-hit rate, tool-calls-per-session histogram.

**Acceptance:** with the env var set locally, a search emits one event with all
attributes; without it, zero behavior change and no new hard dependency at import time.
Running cost: < 5 €/month at current volume.

---

# Phase 1 — one precise call instead of ten guesses

## T6 — Generic zero-hit relaxation (kills the retry spiral)

**Problem:** `total == 0` forces the LLM into sequential filter-guessing (observed
minutes of wall clock). Server-side, one leave-one-out pass costs ~0.3 s after T1.

**Where:** `service/search.py`, after total is known to be 0 and ≥ 2 filters are active.

**Mechanism (filter-agnostic — works for every current and future filter):**
1. Enumerate active filter fields via `SearchFilters.model_fields_set` minus defaults
   (status="all" doesn't count as active).
2. For each active field `f` (cap at 8, run in the T4 thread pool): COUNT with `f` reset
   to its default. Reuse `_build_where` on a `filters.model_copy(update={f: None})`.
3. For numeric range pairs (`*_min`/`*_max`) treat the pair as ONE relaxation unit and
   additionally fetch `SELECT VALUE MIN/MAX(<path>)` over the other-filters result set to
   suggest the nearest achievable range.
4. Respond with (models in `core_at/models/mcp.py`):
   ```python
   class Relaxation(BaseModel):
       dropped: str                      # e.g. "postal_code" or "bilanzsumme_range"
       total: int                        # matches if this one filter is removed
       suggestion: str | None = None     # e.g. "nearest bilanzsumme range 0.8M–4.2M"

   class SearchResponse(BaseModel):
       ...
       relaxations: list[Relaxation] | None = None  # only present when total == 0
   ```
5. In-memory twin: same loop over `_matches` — trivial.
6. Mention the field in the `search_companies` docstring (one sentence: "if total is 0,
   `relaxations` tells you which single filter to drop/loosen — prefer it over guessing").
   Docstring-only → coordinate with owner per ground rule 4, but this sentence is the
   payoff; ship together with T7.

**Acceptance:** unit test: 3 active filters, zero hits → 3 relaxations with correct
counts; live: a zero-hit query answers < 1.5 s including relaxations.

## T7 — **[SCHEMA]** Tool documentation upgrade + ÖNACE catalog in describe_fields

**Problem:** the LLM doesn't know (a) `name` is substring, (b) which field represents
"industry as concept" vs "activity free text", (c) any ÖNACE codes → it guesses.

**Where:**
1. `…/mcp_server/src/fbl_mcp_server/app.py::search_companies` docstring (~line 441).
   Append a compact "query recipes" section (keep the existing text):
   ```
   Query recipes (pick ONE primary strategy per user intent):
   - Specific company by name: filters={"name": "<name>"} — substring, case-insensitive.
     Results are relevance-ordered (exact/prefix matches first).
   - Industry as a CONCEPT ("tech companies", "Metallverarbeiter"): use oenace_division /
     oenace_group (codes + German labels via describe_fields), NOT geschaeftszweig.
   - Industry by literal ACTIVITY TEXT: geschaeftszweig matches the Firmenbuch free-text
     description as substring ("anlagenbau" works; "technisch" won't — it's not semantic).
   - Region: bundesland (broad) > city (exact town) > postal_code prefix. Radius: near.
   - Zero hits? Read `relaxations` in the response and adjust THAT filter; do not retry
     blind variations.
   ```
2. `…/mcp_server/src/fbl_mcp_server/service/records.py::describe_fields` (~line 180):
   add a `oenace_divisions` code table: list of `{division, label_de}` for all ÖNACE 2025
   divisions. Source the labels from the existing classification data in
   `products/agentic-firmenbuch/packages/core_at/src/fbl_core_at/classification/`
   (`grep -rn "division_label_de" …/core_at` to find the label table; do NOT hand-type).
   Also update the mirrored description in the tool docstring if it enumerates tables.

**Acceptance:** describe_fields returns ~88 divisions with German labels; docstring fits
in < 40 additional lines; existing description tests green.

## T8 — Response diet: drop nulls + dedupe ÖNACE-2008 labels (−30–40 % tokens)

**Where:**
1. `McpService.search_companies` in `app.py` (~line 112) calls
   `.model_dump(mode="json")` — change to `.model_dump(mode="json", exclude_none=True)`.
   ⚠️ Check `…/mcp_server/src/fbl_mcp_server/plans.py::flatten_free_search_response`
   (free-plan card flattening) — make it tolerant of missing keys (`dict.get`).
2. In `_card` (`service/_common.py` ~line 219): set the four `oenace_*_2008*` fields to
   `None` when they equal their 2025 counterparts (division==division etc.) — the card
   stays self-explanatory when the vintages differ, and silent when they don't.
3. `has_guv_latest: bool = False` and `is_financial_institution: bool = False` keep
   serializing (they're not None) — fine.

**Acceptance:** a Novomatic search card serializes without `"street": null, …` noise;
free-plan tests green; a golden-card snapshot test updated.

## T9 — Echo normalized filters (`applied_filters`)

**Where:** `SearchResponse` (response-only): `applied_filters: dict[str, Any] | None` —
the filters as actually applied after normalization ("Wien"→"W" via `_BL_NAME_TO_CODE`,
GmbH-family mapping via `_is_gmbh_filter`, clamped page_size). Build it in
`service/search.py` from the same values `_build_where` binds. The LLM immediately sees
mis-parsed inputs instead of silently getting wrong results.

---

# Phase 2 — intent-reflecting ranking

## T10 — Name-relevance re-ranking (candidate pool)

**Problem (measured):** searching "Novomatic" ranks the micro subsidiary above NOVOMATIC
AG (no Bilanzsumme → sorts last). Match quality must dominate when the user is *finding a
company*, not *screening a market*.

**Where:** `service/search.py::search_companies`. When `filters.name` is set AND
`total <= POOL_LIMIT` (module constant, 200):
1. Fetch up to POOL_LIMIT cards (single query, no ORDER BY needed) instead of one page.
2. Re-rank in Python with a **fixed, intent-independent text score**
   (new pure function `_name_match_score(query: str, name: str) -> tuple` in search.py,
   unit-tested):
   exact (casefold) > prefix > word-boundary start > substring; tiebreakers:
   `len(query)/len(name)` ratio desc, then the requested/default sort value desc, then fnr.
3. Slice the requested page from the re-ranked pool. `total` stays exact.
4. If `total > POOL_LIMIT`: current behavior (numeric sort) — that's a screening query,
   document the threshold in the docstring (one line).
5. In-memory twin: same function (it IS Python) — just route both branches through it.

**Acceptance:** test: docs [„NOVOMATIC Sports Betting Solutions" (BS=114k), „NOVOMATIC AG"
(BS=null)], query "novomatic" → AG first. Latency budget: +≤0.3 s on name searches.

## T11 — **[SCHEMA]** Precomputed intent scores + weighted `rank_by`

**Concept:** materialize a few normalized 0–100 scores per company at derive time (from
the ALREADY EXISTING `size.peer_percentiles` — see
`products/agentic-firmenbuch/packages/30_derive/src/fbl_derive/derive.py::_build_size`,
~line 137), index them, and let the LLM sort by one (fast path) or a weighted mix
(pool re-rank). Intent → weights is the LLM's job; the server stays generic.

**v1 score formulas** (pure functions in `fbl_derive`, unit-tested; missing inputs → score
absent, never fabricated):
- `scores.growth` = mean of available `bilanzsumme_5y_cagr` / `eigenkapital_5y_cagr`
  percentiles; if none, map `growth.profile` {fast_growing:85, growing:65, stable:50,
  shrinking:20}; if neither → absent.
- `scores.solidity` = `equity_ratio` percentile; absent if unknown.
- `scores.scale` = `bilanzsumme` percentile **within the whole dataset** (not per-gkl —
  add this percentile in `_build_size`'s cohort call with cohort key "all").
- `scores.basis` = list of the inputs actually used (honesty field, serves as
  `score_basis` on cards).

**Changes:**
1. `core_at/models/…` Size/Derived/Presented models: add `scores: dict[str, float] | None`
   top-level on the presented doc (NOT inside `size`; flat path for indexing). Wire
   through `10_present/src/fbl_present/present.py::present` (~line 69).
2. Bicep + live index paths: `/scores/growth/?`, `/scores/solidity/?`, `/scores/scale/?`.
3. `service/search.py::_SORT_PATHS` (~line 274): add
   `"score_growth": ("scores","growth")` etc. — single-signal intent sorting now rides
   the existing two-bucket machinery unchanged. Also: **reject unknown sort fields with a
   bad_request error listing valid fields** (today an unknown field silently drops
   ordering — `_SORT_PATHS.get` returns None).
4. `Sort` model: add optional `rank_by: list[RankSignal] | None`
   (`RankSignal(BaseModel): signal: Literal["growth","solidity","scale"]; weight: float = 1.0`).
   Weighted path: fetch pool (top-POOL_LIMIT per signal via indexed ORDER BY, union),
   score = Σ weight·percentile (missing → skip signal for that doc, renormalize),
   re-rank, page. Reuses T10's pool mechanics.
5. Docstring: document signals + one example ("wachstumsstark & solide" →
   `rank_by=[{signal:"growth",weight:0.7},{signal:"solidity",weight:0.3}]`).
6. **Backfill:** scores appear only after a re-present run. The pipeline job for that is
   `job-firmenbuch-backfill-process` (re-presents from 30_derived; see
   `orchestration/pipeline.py::process_backfill`). Document the run command in the PR;
   owner triggers it (est. one-time ~3 € RU).

**Acceptance:** derive unit tests for the three formulas incl. absent-input cases;
search test sorting by `score_growth` with missing-score docs in bucket B; weighted-mix
test with hand-computed expected order.

---

# Phase 3 — real radius search

## T12 — **[SCHEMA]** PLZ centroids → `location.geo` + `near` filter

**Data:** GeoNames postal codes AT (CC-BY 4.0, ~2,200 rows: PLZ, place name, lat, lng —
http://download.geonames.org/export/zip/AT.zip). Check in as
`products/agentic-firmenbuch/packages/core_at/src/fbl_core_at/mapping/plz_geo.json`
(`{"1010": {"lat": 48.209, "lng": 16.37, "place": "Wien"}, …}`) plus a generator script
`scripts/build_plz_geo.py` (download → normalize → JSON; committed output, script for
reproducibility). Add CC-BY attribution to `NOTICE`.

**Pipeline:** in `10_present/src/fbl_present/present.py::present` (~line 74, where
`location=company.location.model_dump(…)` is built): look up the doc's postal_code and
attach — new helper `fbl_core_at.geo.plz_centroid(postal_code) -> tuple | None`:
```json
"location": {
  …existing…,
  "geo":  {"type": "Point", "coordinates": [16.37, 48.209]},
  "lat": 48.209, "lng": 16.37,
  "geo_precision": "plz_centroid"
}
```
(`lat`/`lng` as plain numbers are the bounding-box fallback path — index both.)

**Bicep + live:** spatial index `{ path: '/location/geo/*', types: ['Point'] }` in the
`spatialIndexes` section of the indexing policy + range paths `/location/lat/?`,
`/location/lng/?`.

**Filter:** in `core_at/models/mcp.py`:
```python
class NearFilter(BaseModel):
    place: str | None = None        # town name, resolved via plz_geo (case-insensitive)
    postal_code: str | None = None  # alternative anchor; exactly one of the two
    radius_km: float = 25.0         # clamp 1..150

class SearchFilters(BaseModel):
    ...
    near: NearFilter | None = None
```
In `_build_where`:
`ST_DISTANCE(c.location.geo, {'type':'Point','coordinates':[@lng,@lat]}) <= @radius_m`.
**Measure ST_DISTANCE RU on live data first** (extend the T-VERIFY script); if a radius
query costs > ~1,000 RU, switch to the generic fallback: indexed lat/lng bounding-box
pre-filter in SQL + exact haversine post-filter in Python over the page pool. Same API
either way. Ambiguous `place` (several towns, e.g. "Neudorf") → `bad_request` error
listing the candidates with their PLZ (generic disambiguation, no silent pick).
Unknown place → `bad_request` with "use postal_code".
In-memory twin: haversine in `_matches`.
Card addition (response-only): `distance_km: float | None` when `near` was used; sort
default for near-queries stays whatever `sort` says — but add `"distance"` to
`_SORT_PATHS`-adjacent handling as a Python-side sort on the pool (distance is computed,
not stored).

**Backfill:** re-present run (same as T11 — batch them into ONE run).

**Acceptance:** unit: PLZ 4865 doc within 30 km of "Vöcklabruck", not within 5 km;
ambiguity error listed; live: „Umkreis 25 km um Gmunden, Bilanzsumme 1–5 Mio" → 1 call,
< 1 s, plausible result set.

---

# Phase 4 — semantic matching (verify prerequisites first, then build)

## T13 — Eval harness (build BEFORE T14; it gates it)

**Two tiers — note costs:**
- **Tier 1 (default, ~free):** direct MCP tool-call replay, no LLM. Golden file
  `products/agentic-firmenbuch/tests/eval/golden_search.yaml`:
  ```yaml
  - intent: "Firma Novomatic finden"
    call: {filters: {name: "novomatic"}}
    expect_top1_fnr: "069548b"
  - intent: "Anlagenbauer in OÖ, wachstumsstark"
    call: {filters: {oenace_division: "28", bundesland: "Oberösterreich"},
           sort: {rank_by: [{signal: growth, weight: 1.0}]}}
    expect_fnrs_in_top25: [ … ]   # curated
  ```
  Runner `scripts/eval_search.py --tier1`: executes against a local server wired to the
  live DB (or `--base-url` + `X-API-Key` for prod), reports top-1 accuracy /
  recall@25 / p50 latency as a markdown table. Cost: RU cents.
- **Tier 2 (optional, budget-capped):** end-to-end LLM loop measuring ROUNDS per intent.
  `--tier2 --model claude-haiku-4-5-20251001 --max-queries 20` via the Anthropic API MCP
  connector. Haiku over 20–40 intents ≈ **< 1–2 € per run** (NOT 20 € — that estimate
  assumed a frontier model over the full set; default to Haiku, run frontier only before
  releases). Print the token bill at the end.

**Acceptance:** tier 1 runs in CI-mode against the in-memory store with fixture docs
(fast, deterministic) AND supports live mode; 30+ goldens covering: name lookup,
concept industry, region, radius, financial screen, zero-hit relaxation.

## T14 — **[SCHEMA]** Hybrid semantic search (`filters.query`): FTS + embeddings, RRF

**STOP-GATE first (30 min):** verify on the live account (serverless, Germany West
Central):
```bash
az cosmosdb update -n cosmos-firmenbuch-xbjux2hw -g rg-firmenbuch-prod \
  --capabilities EnableServerless EnableNoSQLVectorSearch          # vector
# full-text search: check capability EnableNoSQLFullTextSearch is accepted in GWC
```
If FTS is unavailable on serverless/GWC → build vector-only + keep indexed CONTAINS as
the lexical leg (documented fallback). If vector also fails → STOP, report to owner.

**Embedding content — ONE vector per company** (decision: single combined vector, one
Cosmos field; separate per-field vectors double storage/RU/freshness cost for no
retrieval gain here — lexical name matching is covered by FTS/CONTAINS + T10):
```python
def embedding_text(doc) -> str:
    # identity.name + activity free text + German ÖNACE labels. NO location (structured
    # filters handle it), NO financials (structured), NO manager names (GDPR/noise).
    return (
        f"{identity.name}. "
        f"Tätigkeit: {industry.geschaeftszweig or company.description or ''}. "
        f"Branche: {oenace.group_label_de or ''}; {oenace.division_label_de or ''}; "
        f"{oenace.section_label_de or ''}."
    )
```
Model: Azure OpenAI `text-embedding-3-small`, `dimensions=512` (EU region for the AOAI
resource per the EU-only policy — West Europe or Sweden Central; batch/offline only,
plus one query-embedding call per `query` search at runtime). New env:
`AZURE_OPENAI_ENDPOINT`, key in Key Vault `kv-firmenbuch-xbjux2hw`.

**Doc fields:** `/embedding` (512 floats), `meta`-adjacent `embedding_hash`
(sha256 of embedding_text — freshness guard). Vector policy (bicep + CLI):
```json
"vectorEmbeddingPolicy": {"vectorEmbeddings": [{"path": "/embedding",
  "dataType": "float32", "distanceFunction": "cosine", "dimensions": 512}]},
"indexingPolicy": {…, "vectorIndexes": [{"path": "/embedding", "type": "quantizedFlat"}],
  "excludedPaths": [{"path": "/embedding/*"}, {"path": "/*"}, …]}
```
FTS (if available): fullTextPolicy on `/identity/name` + `/company/description`
(language `de-DE`) + fullTextIndexes on both.

**Pipeline freshness:** new module
`products/agentic-firmenbuch/packages/orchestration/src/fbl_orchestration/embeddings_sync.py`,
mirroring the structure of `industry_sync.py` (same package): iterate docs where
`embedding_hash` ≠ hash(embedding_text) (or absent), batch-embed (batches of 500),
patch docs. Hook it as a step of the daily job (`job-firmenbuch-daily` entry point —
see `orchestration/__main__.py` for how daily steps are registered) + one backfill run.
Geschäftszweige rarely change → steady state is a handful of embeds/day.

**Query path:** `SearchFilters.query: str | None`. When set: embed the query (runtime
AOAI call, ~50–150 ms), then hybrid:
- FTS available: `ORDER BY RANK RRF(FullTextScore(c.identity.name, [@q...]),
  FullTextScore(c.company.description, [...]), VectorDistance(c.embedding, @qvec))`
  with the other WHERE filters intact, TOP pool → page.
- Fallback: `ORDER BY VectorDistance(c.embedding, @qvec)` TOP pool, plus a parallel
  indexed-CONTAINS leg on name+description, RRF-merge the two lists **in Python**
  (`rrf_score = Σ 1/(60+rank)`) — generic, no per-query logic.
Card addition (response-only): `match_reason: str | None`
(e.g. `"semantic: Branche Bauinstallation"` / `"text: name match"` — from which leg(s)
ranked it). In-memory twin: `query` falls back to substring-OR over name+description
(tests don't need real vectors).
Docstring: "query = free-text semantic search (concepts work: 'technische Betriebe');
combine freely with all structured filters."

**Costs (for the record):** one-time embeddings ~14M tokens ≈ <1 €; re-upsert ~3 €;
running: +0.7 GB storage ≈ 0.2 €/mo, delta embeds <1 €/mo, query embeds ~0.
**Gate:** T13 tier-1 concept-intent recall@25 must beat the ÖNACE-only baseline,
else don't ship the query path.

---

# New Cosmos document schema (10_presentation) — delta only

```jsonc
{
  // …all existing fields unchanged…
  "scores": {                       // T11 (absent until backfill ran; fields absent when basis missing)
    "growth": 78.0, "solidity": 55.0, "scale": 91.0,
    "basis": ["bilanzsumme_5y_cagr", "equity_ratio", "bilanzsumme"]
  },
  "location": {
    // …existing…,
    "geo": {"type": "Point", "coordinates": [16.37, 48.209]},   // T12
    "lat": 48.209, "lng": 16.37, "geo_precision": "plz_centroid"
  },
  "embedding": [/* 512 float32 */],   // T14, excluded from range index
  "embedding_hash": "sha256:…"        // T14 freshness guard
}
```

**Indexing policy delta:** + `/identity/name/?` (T1), `/scores/growth/?`,
`/scores/solidity/?`, `/scores/scale/?` (T11), spatial `/location/geo/*` Point +
`/location/lat/?`, `/location/lng/?` (T12), vectorIndex `/embedding` quantizedFlat +
exclude `/embedding/*` (T14). Everything else unchanged.

# New MCP surface — delta only (`core_at/models/mcp.py`)

```python
class NearFilter(BaseModel):                     # T12
    place: str | None = None
    postal_code: str | None = None
    radius_km: float = 25.0

class SearchFilters(BaseModel):
    ...existing fields unchanged...
    near: NearFilter | None = None               # T12 [SCHEMA]
    query: str | None = None                     # T14 [SCHEMA] semantic free text

class RankSignal(BaseModel):                     # T11
    signal: Literal["growth", "solidity", "scale"]
    weight: float = 1.0

class Sort(BaseModel):
    field: str | None = None                     # now also: score_growth|score_solidity|score_scale
    descending: bool = True
    rank_by: list[RankSignal] | None = None      # T11 [SCHEMA]

class Relaxation(BaseModel):                     # T6 (response-only)
    dropped: str
    total: int
    suggestion: str | None = None

class CompanyCard(BaseModel):
    ...existing...                               # T8: serialize with exclude_none
    distance_km: float | None = None             # T12 (response-only)
    match_reason: str | None = None              # T14 (response-only)

class SearchResponse(BaseModel):
    ...existing...
    has_more: bool = False                       # T4 (response-only)
    applied_filters: dict[str, Any] | None = None  # T9 (response-only)
    relaxations: list[Relaxation] | None = None  # T6 (response-only)
```

# T-VERIFY — measurement script (create first, run after every phase)

Create `scripts/measure_search_perf.py`: parameterized copies of the 2026-07-12
measurement queries (name-CONTAINS count/page, indexed-filter page, radius query once T12
lands), printing latency + RU per query and a before/after table. Auth via
`DefaultAzureCredential` + `COSMOS_ENDPOINT` env (endpoint:
`https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/`). Baseline numbers to beat
are in the header of this file.

# Suggested commit/PR slicing

1. PR-1: T1+T2+T3+T4 (+T-VERIFY) — pure speed, no schema changes, ship immediately.
2. PR-2: T5 telemetry.
3. PR-3: T6+T8+T9 (+T7 text prepared but gated on owner's directory-review timing).
4. PR-4: T10; PR-5: T11 (+backfill runbook); PR-6: T12 (+same backfill run);
5. PR-7: T13; PR-8: T14 (behind the stop-gate).
```
