# `tests/e2e/` — true end-to-end smoke test (live API → all layers → MCP)

A single test that runs a **small, configurable set of real FNRs** live through the whole
pipeline and finishes with an MCP query against the result:

```
firmenbuch_client → 90_raw → 70_parsed → 50_consolidated → 30_derived → 10_presented → MCP
```

It is **separate from the fixture-based unit/integration tests** and **skipped by default**
(including in CI). It uses **in-memory** Blob/Cosmos stores and a **tiny real pull** (a few
FNRs) — it never provisions Azure and never runs the full backfill.

## Run it
```bash
# needs a Firmenbuch HVD key (read from the env, or the repo-root .env)
FBL_E2E=1 uv run pytest tests/e2e -q

# choose the companies (comma-separated FNRs; default: 030435h,030636d)
FBL_E2E=1 FBL_E2E_FNRS=030435h,030636d,490875a uv run pytest tests/e2e -q
```
Env:
- `FBL_E2E=1` — required to enable (otherwise the test is skipped).
- `FIRMENBUCH_API_KEY` — the HVD key (or set it in `.env`).
- `JUSTIZONLINE_API_URL` — optional override (defaults to the JustizOnline HVD URL).
- `FBL_E2E_FNRS` — optional comma-separated FNRs to exercise.

## What it asserts
Real artifacts land in `90-raw` (XML + master `auszug`); a stored raw XML re-parses;
`consolidate`/`derive`/`present` produce docs in `50/30/10` for every FNR; the served
doc is well-formed and carries **no officer name** (GDPR); and the MCP tools
(`search_companies`, `get_company_details`, `get_company_history`) return valid responses.

---
↑ [Repo root](../../../../README.md) · [AT product](../../README.md) · Fixtures: [tests/](../README.md)
