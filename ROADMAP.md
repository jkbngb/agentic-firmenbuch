# Roadmap & backlog

Public, living backlog for **agentic-firmenbuch**. The core pipeline (ingest → parse →
consolidate → derive → present) and the MCP serving layer are in production; the items
below are post-baseline hardening and coverage work. Each line is tracked as a GitHub
issue — see the [issue tracker](https://github.com/jkbngb/agentic-firmenbuch/issues).

## Status

- **Data integrity:** independently audited — nothing lost and every field correctly
  mapped across ~15k line-item mappings, zero defects. The transform is trustworthy.
- **Serving layer:** OAuth connect/refresh fixed (expired tokens now trigger a silent
  refresh instead of a permanent disconnect). All legal forms (GmbH, AG, KG, OG, …) are
  served with financials.

## Backlog

### Data freshness & coverage
- **Stabilise the scheduled daily delta run.** The change-feed delta job is enabled
  (`0 3 * * *`) and succeeds on retry, but the 03:00 scheduled run is flaky and emits
  failure alerts. Diagnose the scheduled-run failure and make it pass first-try.
- **Expand served coverage toward the full register.** ~341k of ~640k registered
  entities are presented; complete the backfill for companies with published accounts
  that are not yet processed (resumable per-Rechtsform run).
- **Include inactive/deleted (`gelöscht`) companies** or confirm the active-only scope is
  intentional; the status-only refresh path is currently unexercised in production.

### Pipeline robustness
- **Harden `urkunde` download for large multi-MB filings.** Large documents return
  `http 200` then fail after retries, dropping big filers to PDF-only on re-ingest/replay.
  Add streaming + better retry so completeness doesn't degrade over time.
- **Verify the `firmenbuch_2025` parse variant on live data.** It passes on fixtures but
  has not been seen in any live filing — confirm it does not occur, or source a real
  sample and add it to live validation.

### Metrics
- **Expose `betriebserfolg` under its own name and (optionally) a strict EBIT.** Today
  `ebit` is the UGB operating result (Betriebserfolg, excludes the financial result) — a
  common approximation, documented in the FAQ / Datenfelder / `describe_fields`. Surface
  the operating result under its correct name and optionally compute strict EBIT
  (pre-tax result + interest) where the GuV positions allow, so consumers can choose.

### Cleanup (done in the baseline-hardening pass)
- ✅ Birth-data privacy invariant guarded in CI (year only; never month/day).
- ✅ Latent `Taxonomy.by_canonical()` crash fixed (`@lru_cache` over a Pydantic model).
- ✅ The two latent taxonomy code collisions pinned by a regression test (resolution is
  first-by-appendix-order; the appendix stays byte-identical to the official docs).
