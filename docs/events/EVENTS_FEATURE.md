# Register events — feature design (issue #16 follow-up)

Status: design + build in progress (2026-07-09). Owner-facing goal: turn the raw "something
changed" derivation into a **customer-facing change-intelligence surface** — per company AND
across the whole register — that powers deal sourcing, monitoring and market watch.

## 1. Where we start

Events are DERIVED, not read: the HVD `auszug` tier returns a company's *current* master data,
not the historical `VOLLZ` change log. So the daily delta re-fetches a changed company's master
and diffs it against the `event_baseline` captured at the previous consolidation
(`50_consolidate/events.py`). Five change types are detected:

| type | trigger |
|------|---------|
| `name_change` | Firmenwortlaut changed |
| `seat_change` | registered seat / address (city, PLZ, street) changed |
| `legal_form_change` | Rechtsform changed |
| `capital_change` | Stammkapital changed |
| `management_change` | vertretungsbefugte Organe added / removed / role change |

Safeguards: forward-only from `EVENTS_START = 2026-07-01` (one clean origin date); the FIRST
observation of a company only establishes the baseline and emits nothing (no spurious flood).

Today the only surface is the per-company `events[]` array on `get_company_details`. There is **no
cross-company query** — you cannot ask "which companies changed management this week". That is the
highest-value gap and what this feature adds.

## 2. Goals / use cases

- **Deal sourcing:** capital increases (growth) and management changes (succession, distress) are
  the classic M&A / origination signals. "GF-Wechsel + Kapitalerhöhung in OÖ, Maschinenbau, letzte
  30 Tage" must be one call.
- **Monitoring / watchlist:** "notify me when any of these 40 FNs changes".
- **Market intelligence:** counts by type / region / sector over time.

## 3. Data model

### 3.1 `RegisterEvent` (enriched, per-company; `core_at/models/company.py`)

Keep `date`, `type`, `description`, `source`; ADD structured, optional fields so an agent can act
on the change without parsing prose (all optional → backward compatible):

- `capital_from: float | None`, `capital_to: float | None` — for `capital_change`.
- `managers_added: list[str]` — e.g. `["GESCHÄFTSFÜHRER Max Mustermann"]` (role + name).
- `managers_removed: list[str]` — same shape.

`description` stays the human one-line summary. Names are public Firmenbuch data
(`EXPOSE_PERSONAL_DATA=true`); birth data stays year-only and is never in an event.

### 3.2 `10_events` — dedicated serve container (new)

Querying the embedded `events[]` across 341k `10_presentation` docs is a cross-partition scan.
Instead the pipeline flattens each derived event into its own doc in a small, indexable container:

- container `10_events`, partition key `/fnr` (small container → cross-partition filter is cheap).
- id `= {fnr}:{date}:{type}` — deterministic + idempotent (re-presenting re-upserts identically;
  event history only grows, so no deletes/staleness).
- fields: `fnr, name, date, type, description, source` + the structured RegisterEvent fields +
  denormalized **facets for filtering**: `bundesland` (code), `oenace_section`, `oenace_division`,
  `legal_form` (code) — the same stored codes the search filters use, so a `list_events` filter is
  symmetric with `search_companies`.

Written from the already-built presented doc (it carries the facets + `events[]`), right after the
`10_presentation` upsert, in BOTH pipeline write paths (`process_set`, parallel `_present`).

## 4. MCP tools (Pro-gated, read-only)

### 4.1 `list_events` — cross-company event feed

Params (all optional, AND-combined):
- `types: list[str]` — subset of the five types.
- `since` / `until` — ISO date bounds (default: last 30 days).
- `bundesland`, `oenace_section`, `oenace_division`, `legal_form` — facet filters (full names like
  "Wien"/"GmbH" mapped to stored codes, as in search).
- `fnrs: list[str]` — watchlist (specific companies).
- `page` (1), `page_size` (25, ≤100).

Returns `{ total, page, page_size, events: [ {fnr, name, date, type, description, capital_from,
capital_to, managers_added, managers_removed, bundesland, industry_section} ] }`, newest first.

### 4.2 `get_event_stats` — aggregate counts

Params: `since`/`until` (default 30d) + the same facet filters. Returns counts by `type` and by
`bundesland` (+ total) — for dashboards / "what's moving in the market".

Both are Pro-only (`gate_pro_only`) — a clear upgrade driver; free stays on search + capped details.

## 5. Semantics customers must understand (documented in `describe_fields` + felder.html)

- Events are **forward-only from 2026-07-01**. An empty result / empty `events[]` for an unchanged
  company is correct, not a gap.
- Events are **derived** from the daily change feed (`source="change_feed_delta"`), typically
  surfaced within a day of the register change; `source="auszug"` marks a rare literal VOLLZ entry.

## 6. Rollout

Pipeline image (writes `10_events`) + MCP image (serves the tools) both ship. `10_events` fills
forward as companies change post-catch-up; the tools return `[]` until then (correct). Verified in
tests with synthetic events now; verified live once real events accrue.
