# Documentation index — agentic-firmenbuch

This folder holds the specifications and reference material for
**agentic-firmenbuch** (Agentic-Firmenbuch.at), the live Austrian Firmenbuch
data product served over a multi-tenant **MCP server**. Up one level:
[repo root README](../README.md) is the master index in pipeline order `90 → 10`.

## Naming convention

**No version numbers in filenames.** Each topic has exactly **one** living
document named `<Thema>_Spezifikation.md`; its history is git, not a `_v1`/`_v2`
suffix. Two buckets:

- **The system as built — the stable record.** `Fachliche_Spezifikation.md`,
  `Technische_Spezifikation.md`, `Distribution_Spezifikation.md` and
  `Rechtsform_Coverage.md` describe the live pipeline + MCP server. They are the
  build contract; they change in place when shipped behaviour changes.
- **The forward plan.** The lightweight, prioritised index lives at the repo
  root: [`ROADMAP.md`](../ROADMAP.md). Its detailed design record is
  [`Erweiterungen_Spezifikation.md`](Erweiterungen_Spezifikation.md). As a chapter
  ships, it is marked done in `ROADMAP.md`; substantial new shipped behaviour is
  folded back into the spec for its topic.

## The documents

### Built / shipped

| Doc | What it is |
|---|---|
| [`Technische_Spezifikation.md`](Technische_Spezifikation.md) | **Primary.** The HOW — architecture, module contracts, schemas, lineage, runbook, edge cases (§15b), build order (§15). |
| [`Fachliche_Spezifikation.md`](Fachliche_Spezifikation.md) | The WHAT/WHY — scope and business rules, implementation-agnostic. |
| [`Distribution_Spezifikation.md`](Distribution_Spezifikation.md) | The go-to-market layer — marketing site, email signup, automated API-key delivery, bot protection, legal. |
| [`FIELD_REFERENCE.md`](FIELD_REFERENCE.md) | Served-field dictionary — every field each MCP tool returns, with type + null-rules. User-facing twin: the public [`felder.html`](https://www.agentic-firmenbuch.at/felder.html) page. |
| [`pipeline-step-samples.md`](pipeline-step-samples.md) | File-format reference — one golden sample document per pipeline stage (also the test fixtures). |

### Supporting evidence

| Doc | What it is |
|---|---|
| [`API_PROBE_FINDINGS.md`](API_PROBE_FINDINGS.md) | Live HVD-API probe that resolved the Technische Spezifikation §16 open items (auth, `auszug`, change feeds, result cap, bulk dataset). Referenced from the client/ingest code. |
| [`Rechtsform_Coverage.md`](Rechtsform_Coverage.md) | Per-Rechtsform coverage analysis — which legal forms yield financials and why, verified end-to-end on live samples. Backs Technische Spezifikation §15b 20a–20c. |

### Forward plan

| Doc | What it is |
|---|---|
| [`../ROADMAP.md`](../ROADMAP.md) | Prioritised status + next-steps index (ingest gap, banks/insurers, GISA, Ediktsdatei). |
| [`Erweiterungen_Spezifikation.md`](Erweiterungen_Spezifikation.md) | Detailed design + evidence for the planned extensions — banks (BWG) / insurers (VAG) handling, FI flag, usage metering, ingest-gap fix. Backed by [`research/`](research/). |
| [`research/`](research/) | Deep-dive research with external-source citations: `banks_BWG_schema.md`, `insurers_VAG_schema.md`, `jab40_bank_insurer_support.md`, `ediktsdatei_insolvency.md` (P4 insolvency API + join design). |

### Reference material (read-only)

| Path | What it is |
|---|---|
| [`appendix_position_mapping.json`](appendix_position_mapping.json) | The 317-entry canonical UGB position taxonomy. **Code depends on this** — `core/mapping/` copies it verbatim. Do not edit. |
| [`reference/`](reference/) | Official source material — JustizOnline API reference, JAb 4.0 XSDs + Excel. Reference only. |
