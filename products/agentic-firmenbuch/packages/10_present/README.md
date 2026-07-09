# `present` (`fbl_present`) — Stage 7 · `30_derived` → `10_presentation`

**Layer:** `10_presentation` | **reads:** Cosmos `30_derived` | **writes:** Cosmos `10_presentation` (+ `10_events`)

`event_records()` flattens a presented doc's `events[]` into standalone `10_events` docs (one per
event, with denormalized facets) — the query index behind the `list_events` / `get_event_stats`
feed (the orchestration pipeline upserts them; design: `docs/events/EVENTS_FEATURE.md`).

**Purpose:** assemble the **public served document**: enforce scope, GDPR gating, and
attribution; denormalize filter fields to shallow indexed paths; upsert by `fnr` (§8.7).

**Curated projection with a no-loss guarantee (Part B §5.1):** present is the one layer
that intentionally surfaces *less* than the layer before it. The exact set of derived
fields NOT surfaced is the **explicit, justified `PRESENTED_ALLOWLIST`** (officer names per
GDPR; the full `financials.positions`/`passthrough` maps; `completeness`; `guv_years`;
`management.signatories_history`; `derivations`; the internal `meta` chain). Everything on
that list **except officer names** is retrievable in full via the MCP `get_full_record`
tool. `tests/test_layer_completeness.py` fails if any derived leaf is dropped that is not on
the allowlist.

## Gating (GDPR, §8.7)
- Officer **names are withheld by default** (`expose_personal_data=false`).
- **Exposed:** `age_at_signing`, current `age`, `birth_year` (year only), `role_label`,
  `n_signatories_latest`, `signatories_stable_years`. **Never** the name, month, or day.
- Names are emitted only when `expose_personal_data=true` (a documented lawful basis).
  The tests assert the name never appears anywhere in the default served body.

## Denormalized index fields (§4.1)
Copies the handful of filter/sort fields to stable shallow paths the Cosmos index
covers: `identity.status`/`legal_form`, `location.bundesland`, `size.gkl`,
`financials.has_guv_latest`, `financials.latest.bilanzsumme`/`revenue`,
`ratios.equity_ratio.latest`, `growth.profile`, `employees.latest`,
`company.last_filing_year`. Status is copied from the registry (the source of truth).

## Other behaviour
- Reserved groups (`sector`/`enrichment`/`score`/`summary`/`observations`) are `null` in v1.
- A trimmed public `provenance` block (source, CC BY 4.0 attribution, `data_version`,
  `built_at`) travels with the data; the internal hash chain lives in `meta` (stored in
  Cosmos, omitted from MCP responses).
- **Status-only refresh** (`present_status_only`): when a company is dirty solely due to
  `dirty_reason=status_change`, re-emit from the existing presented doc — no re-derive.
- Idempotent: `id == fnr`; identical input → identical `content_hash`.

## Run it standalone
```bash
uv run pytest packages/10_present
```

## Definition of Done (§8.7) — met
Served doc validates against the model; no officer names when gating is on; attribution
present; indexed filter fields populated; status-only refresh works. `ruff` +
`mypy --strict` + `pytest` green.

## Place in the pipeline
**Previous:** [`derive`](../30_derive/README.md) · **Next:** served by
[`mcp_server`](../mcp_server/README.md) (Stage 9) from `10_presentation`.

---
↑ [Repo root](../../../../README.md) · Specs: [Technische §8.7 / §4.1](../../../../docs/specs/Technische_Spezifikation.md) · [Pipeline samples](../../../../docs/pipeline-step-samples.md)
