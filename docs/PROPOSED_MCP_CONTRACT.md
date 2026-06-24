# Proposed MCP Contract — tools + served columns

## ✅ Owner sign-off (2026-06-18)
- **UGB position codes:** **exposed** (official code + §-label per Postenzeile).
- **Officer names:** **off** (GDPR) — serve only `age` / `age_at_signing` / `birth_year` (year) / role / Vertretungsart. `EXPOSE_PERSONAL_DATA=false`.
- **Tools (owner: no preference → recommendation adopted):** public = `search_companies`,
  `get_company_details`, `get_company_history`, `get_full_record`, `get_document`,
  `list_sectors`, `get_cohort_summary`, `find_peers`. `get_coverage` = **internal/diagnostic** (not advertised).
- **GF-age filter (no preference → added):** `gf_age_min` added to `search_companies` and to the
  playground intent parser ("GF über 60" / "Nachfolge" → `gf_age_min`, default 60). ✅ implemented.

**Remaining before go-live:** wire the playground **LLM mode** (Claude Haiku tool-calling) to
these tools/columns and flip `playground_llm_enabled` — needs an Anthropic API key + explicit
go-ahead (cost). Deterministic mode is live until then.

---

# Proposed contract detail (as confirmed above)

**Status: STOP / awaiting confirmation (Distribution Spez §13a).** This document proposes the
exact **MCP tools** (functionalities) and **served columns** (the `10_presentation` fields the
product exposes). Per the build plan, everything that does *not* depend on this contract is
already built (site, signup Functions, ACS email, the full playground UI + deterministic
backend + guards). The two things that wait for your confirmation:

1. the **MCP tool list** (names, inputs, outputs), and
2. the **served field set** in `10_presentation`,

before I (a) finalize the MCP server's exposed surface and (b) wire the playground's **LLM
tool-calling** to it. **Nothing below is locked yet — please confirm or adjust.**

> Auth: every tool is called over the MCP server authenticated by **`X-API-Key`** (the issued
> key). Per-key rate limits apply (default 60/min, 5 000/day; config). The `token` argument in
> the code is that key.

---

## 1. Proposed MCP tools (as currently built in `fbl_mcp_server`)

| # | Tool | Inputs | Output | Notes / keep in v1? |
|---|------|--------|--------|---------------------|
| 1 | **search_companies** | `filters` (see §3), `sort`, `page`, `page_size` | `{ results: CompanyCard[], total, page, page_size }` | Core discovery. **Keep.** |
| 2 | **get_company_details** | `fnr` | Curated presented doc (§2, minus allowlisted §4) | Core drill-down. **Keep.** |
| 3 | **get_company_history** | `fnr`, `metrics[]` | Per-year time series for the requested metrics (Bilanzsumme, Eigenkapitalquote, …) | **Keep.** |
| 4 | **get_full_record** | `fnr` | Complete presented doc **incl.** the allowlisted detail (full position taxonomy, passthrough, completeness, GuV years, signatory history, formula registry) — still GDPR-gated | Power users. Keep? |
| 5 | **get_document** | `doc_key` | Original Jahresabschluss document reference (link/metadata) | Keep? (links the source filing) |
| 6 | **list_sectors** | — | Sector buckets + counts | Keep? |
| 7 | **get_cohort_summary** | `dimension`, `value` | Aggregate stats for a cohort (e.g. all GmbHs in Steiermark) | Optional v1 — keep? |
| 8 | **find_peers** | `fnr`, `n` | Nearest companies by Bilanzsumme within the same size class | Optional v1 — keep? |
| 9 | **get_coverage** | — | Universe coverage / parse-success diagnostics | **Internal/diagnostic** — expose publicly or restrict? |

**Decisions wanted:** which of #4–#9 are public in v1, and do you want any tool **renamed** or
any **new** tool (e.g. an explicit "succession candidates" / GF-age screen — see §3 note).

---

## 2. Served columns — `get_company_details` (curated presented doc)

Top-level blocks of the served document (nested JSON):

- **identity** — `fnr`, `name`, `legal_form`, `status` (active/historical/deleted)
- **location** — `bundesland`, `sitz` (Ort), `court` (Firmenbuchgericht), `euid`
- **company** — `last_filing_year`, registration/age fields
- **size** — `gkl` (size class W/K/M/G), `bilanzsumme_band`
- **financials** — `latest_year`, `latest { bilanzsumme, revenue, … }`, `has_guv_latest`
- **ratios** — `equity_ratio`, debt/coverage ratios, each `{ latest, history[] }`
- **growth** — `profile` (shrinking/stable/growing/fast_growing), growth rates
- **employees** — `latest` (where published)
- **management** — **GDPR-gated, see §5**: `age_at_signing`, `age`, `birth_year` (year only),
  `role_label`, `vertretung` (Vertretungsart), `n_signatories_latest`, `signatories_stable_years`
- **filings[]** — list of known filings (Stichtag, format, doc_key)
- **events[]** — register events
- **provenance** — source + CC BY 4.0 attribution + `data_version`, `built_at`
- **official UGB codes** — each Postenzeile carries its **official source code** (e.g.
  `HGB_224_2_A_II`) + a human §-label (e.g. „§224 Abs 2 A II"). **Confirm exposing the codes.**

### Search-result card (`search_companies` results) — the compact subset
`fnr`, `name`, `legal_form`, `bundesland`, `size_gkl`, `bilanzsumme_latest`,
`equity_ratio_latest`, `revenue_latest`, `growth_profile`, `has_guv_latest`.

---

## 3. Search filters (`search_companies` inputs)

`status` (active/inactive/all), `legal_form`, `bundesland`, `size_gkl` (W/K/M/G),
`bilanzsumme_min/max`, `equity_ratio_min/max`, `revenue_min/max`, `employees_min/max`,
`growth_profile`, `has_guv` / `has_guv_latest`, `last_filing_year_min`.

> **Note / gap to confirm:** there is **no Geschäftsführer-age filter** in v1 (the landing
> page shows a „GF über 60 – Nachfolge" example). `management.birth_year`/`age` are *served per
> company*, but not yet a *search filter*. Add a `gf_age_min` filter (and/or a "succession"
> convenience) — yes/no?

---

## 4. NOT served in the curated doc (allowlist) — retrievable via `get_full_record`

These are deliberately curated out of `get_company_details` (size/clarity) but **not lost** —
available via `get_full_record`:

- `financials.positions` — full 317-entry position taxonomy map
- `financials.passthrough` — unknown source codes + history
- `financials.completeness` — per-year item-count QA metric
- `financials.guv_years` — list of GuV years (`has_guv_latest` *is* surfaced)
- `management.signatories_history` — per-year signatory-count series
- `derivations` — `metrics_version` + ratio/growth formula registry
- `meta` / `_meta` — internal lineage + content-hash chain — **never served**

**Confirm** this split (curated vs. full) is what you want, or move any field into the default
served doc.

---

## 5. GDPR gate (already enforced — confirm it stays)

Officer **names are withheld** (`management.primary_gf.first_name`/`last_name` are in the
allowlist and only emitted if `EXPOSE_PERSONAL_DATA=true`, which is **off**). What *is* served:
`age_at_signing`, current `age`, and **`birth_year` (year only — never month/day)**, plus
`role_label` and `vertretung`. **Confirm: keep names off for v1.**

---

## 6. After you confirm

On your sign-off (with any edits to §1–§5) I will:
1. finalize the MCP tool list + served fields (remove/rename/add per your notes),
2. wire the **playground's LLM mode** (Claude Haiku tool-calling) to exactly these tools/columns,
3. flip the playground from deterministic to LLM mode via the existing `playground_llm_enabled` flag.

Until then the playground runs in the **deterministic** mode (already live), and the MCP server
keeps its current tool set unchanged.
