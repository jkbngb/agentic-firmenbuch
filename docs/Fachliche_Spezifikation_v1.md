# agentic-firmenbuch — Fachliche Spezifikation

**Version:** 1 (everything herein is Version 1 unless explicitly stated otherwise)
**Status:** For sign-off
**Language note:** English prose; Austrian/accounting **Fachbegriffe kept in German** (Jahresabschluss, Bilanz, GuV, Eigenkapital, Geschäftsführer, Verrechnungsstelle, Größenklasse, etc.).
**Companion document:** *Technische Spezifikation v1* (the "how"). This document is the "what & why" and is implementation-agnostic.

---

## 1. Purpose & vision

Build a **live, always-current data product over the Austrian Firmenbuch**, delivered as a **multi-tenant MCP server** that anyone can sign up for with an email address. A fully automated pipeline queries the official **Firmenbuch HVD API** on a schedule, ingests every newly published **Jahresabschluss** and register change for the **entire company universe** (the register holds ~640k legal entities; the served slice is currently ~341k and grows as the backfill progresses), turns the raw filings into clean, consolidated per-company records, and computes everything that is **objectively derivable** from Firmenbuch data (multi-year financial time series, ratios, growth, trends). Consumers query it through the MCP server.

The MCP server is the product. The end-to-end data pipeline behind it is the bulk of the work. Version 1 ships **facts and clean derivations only** — no scoring, no third-party data, no AI text.

---

## 2. Scope

### 2.1 The guiding rule
Version 1 contains **only what is objectively derivable from the free Firmenbuch (HVD) data**:
- **Tier 1 — raw facts** taken directly from the API (company master data, filings, financial statement line items).
- **Tier 2 — clean derivations** that are deterministic, reproducible functions of Tier 1 (time series, ratios, growth rates, trends, size bands, peer percentiles).

Anything requiring a model, a judgement, an LLM, or an external data provider is **out of scope for Version 1**.

### 2.2 In scope (Version 1)
- Automated, scheduled ingestion of new filings and register changes for **all** companies.
- Parsing of **structured XML** Jahresabschlüsse (both formats — see §4.2) into a canonical schema.
- Storage and **linking of the original PDF** for every filing (even when not parsed).
- Consolidation into per-company records with **multi-year** Bilanz and GuV time series.
- Deterministic **ratios, growth, trends, size bands, peer percentiles**.
- A **coverage dashboard** (internal) showing data completeness across the universe.
- An **MCP server** with search, company detail, history, document access, and cohort tools.
- **Email signup → API token**, free tier with rate limits.
- Full **data lineage / provenance** on every record.

### 2.3 Out of scope (deferred to later versions)
- **Scoring** of any kind (exit-window score, succession score, quality bands, signals).
- **Third-party enrichment** (Northdata ownership/structure, SerpAPI online presence, etc.).
- **NACE / sector classification** — not provided by the free API.
- **AI-generated text summaries.**
- **OCR / extraction from PDF-only filings.**
- **Paid subscription tiers** and **marketing website** (only a minimal signup surface in v1).
- **Gesellschafter / ownership data** (not in the free API).

> The architecture **reserves clean seams** for all of the above so they can be added later without reworking the core (see *Technische Spezifikation*).

---

## 3. Data source — Firmenbuch HVD

The data comes from the official **Firmenbuch HVD (High Value Datasets)** web service operated by the Austrian Ministry of Justice (BMJ).

- **Free to use.** Under the EU Open Data Directive (2019/1024) and Implementing Regulation (EU) 2023/138 (Annex 5), Austria must publish Firmenbuch base data — including Jahresabschlüsse — free of charge and for reuse, including commercial reuse. (Confirmed in practice: API keys are issued at no cost — no fees, no credit card.)
- **Licence: CC BY 4.0** — reuse permitted **with attribution**.
- **What it provides (functional view):**
  - **Company identity (reliable):** Firmenbuchnummer (FNR), name, legal form (Rechtsform), status, registering court (Gericht), and seat (Sitz) — from the company search.
  - **Filing inventory:** which Jahresabschlüsse exist, for which Stichtag, with size class (Größenklasse / GKL), and submission dates.
  - **Filing content:** the actual Jahresabschluss as **structured XML** or as **PDF**. The XML also contains **Stammkapital** and the **signing person** (Geschäftsführer name + birth date) per filing.
  - **Change feeds:** newly published documents and company changes within a date range — the intended basis for efficient daily updates *(availability on our tier to be confirmed against the key — see §10)*.
  - **Bulk dataset:** the whole Firmenbuch is additionally published as a downloadable **HVD bulk dataset on data.gv.at**, refreshed daily — the intended seed for enumerating all ~640k legal entities.
- **Tier limitation (important):** testing against the free HVD tier indicates the detailed company extract (`auszug` / Kurzinformation) **does not work on it**. Consequently, in Version 1, full **street address / PLZ**, the complete **officer list**, and the **Geschäftszweig** (business purpose) are **not reliably available** and are treated as deferred; `Bundesland` is derived from the court/seat, and Stammkapital + the signing Geschäftsführer come from the **Bilanz-XML**. *(To be confirmed against the key before this is locked — see §10.)*
- **Not available from the free API (therefore deferred):** NACE code, Gesellschafter (shareholders) and ownership percentages, corporate group structure, online presence.

---

## 4. Data captured

### 4.1 Data dictionary (Version 1)

| Group | Fields (Fachbegriffe) | Status |
|---|---|---|
| **Identity** | `fnr`, `name`, `register_id`, `legal_form` (Rechtsform), **`status` (active / historical / deleted = gelöscht)**, `court` (Gericht) | In |
| **Location** | `bundesland`, `city`, `postal_code`, `street`, Sitz | In |
| **Company master** | `stammkapital`, `first_filing_year`, `last_filing_year`, `filing_years_available`, `description` (Geschäftszweig) | In |
| **Size** | `gkl` (Größenklasse K/M/G, from API), `band`, `peer_percentiles` | In |
| **Bilanz** | Bilanzsumme, Eigenkapital, Verbindlichkeiten, Anlagevermögen, Umlaufvermögen, Sachanlagen, Finanzanlagen, Vorräte, Forderungen, Kassenbestand (cash), Rückstellungen, Stammkapital, Kapitalrücklagen, Gewinnrücklagen, Bilanzgewinn/-verlust — each as a **multi-year time series** | In (XML filings only) |
| **GuV** | Umsatzerlöse / Rohergebnis (revenue_basis), Personalaufwand, Abschreibungen, EBIT, EBITDA, Jahresüberschuss — multi-year time series | In, **when filed** (see §5.2) |
| **Filing flags** | `has_bilanz`, `has_guv`, `has_guv_latest`, `guv_years`, `has_xml`, `has_pdf_only`, `revenue_basis` | In |
| **Filing documents** | per filing: `format`, `parsed`, stored **PDF + link** | In (PDF for all; financials parsed for XML only) |
| **Employees** | `employees` (Arbeitnehmer, from filing notes) | In, when present |
| **Ratios** | Eigenkapitalquote, Verschuldungsgrad, Debt/Equity, Working-Capital-Ratio, Anlagedeckungsgrad I, EBIT/EBITDA/Net margin, ROA, ROE, capital profile | In (where inputs exist) |
| **Growth** | per-line YoY + 3y/5y CAGR + volatility; `growth.profile` | In |
| **Trends** | per-ratio trend, rolling avg/min/max, volatility | In |
| **Management (facts)** | signatory **age at signing** + **birth year (year only)**, role, signatory count/stability — **name withheld**, no month/day (§7) | In |
| **Events** | register events (name/seat/legal-form/function/capital changes) | In |
| **Sector (NACE)** | — | Deferred |
| **Ownership / Gesellschafter** | — | Deferred |
| **Scoring / enrichment / summary / online presence** | — | Deferred |

> **`auszug`-dependent fields (subject to §3 tier limitation, to be confirmed):** full `postal_code` / `street`, the complete officer list, and `description` (Geschäftszweig) rely on the company extract, which appears unavailable on the free HVD tier. If so, in Version 1: `location` = Sitz + derived Bundesland (no street/PLZ); `management` = the **signing Geschäftsführer from each Bilanz-XML** only (not a full officer list); `description` deferred. `stammkapital` still comes from the Bilanz-XML.

### 4.2 Two filing formats + PDF (a Version 1 reality)
A Jahresabschluss arrives in one of three shapes, and the product must handle all three:
1. **Legacy FinanzOnline XML** (`HGB_224_*` positional structure) — historical filings up to end-2025.
2. **JAb 4.0 XML** (semantic structure, e.g. `BILANZ_EIGENKAPITAL`) — the mandatory format from 2026-01-01 onward.
3. **PDF only** — many filings (esp. small companies / older years) carry no structured data.

**Rules:**
- Both XML formats are parsed into the **same canonical schema**; the consumer never sees the difference.
- **Version 1 parses XML only.** PDF-only filings are **not** financially parsed (no OCR in v1).
- **Every filing's PDF is stored and linked** regardless, so the original Jahresabschluss is always retrievable from the MCP — even for PDF-only companies.
- We do **not** assume how large the PDF-only share is; the **coverage dashboard** measures it (§6.3).

---

## 5. Derived metrics & business rules

### 5.1 Ratios (computed deterministically; where inputs exist)
Eigenkapitalquote (Eigenkapital / Bilanzsumme), Verschuldungsgrad (debt ratio), Debt/Equity (Verbindlichkeiten / Eigenkapital), Working-Capital-Ratio, Anlagedeckungsgrad I (Eigenkapital / Anlagevermögen), EBIT-Marge, EBITDA-Marge, Net margin (Jahresüberschuss / revenue), ROA, ROE, plus a `capital_profile` classification. Each ratio carries per-year history, `avg_3y`/`avg_5y`, `min_5y`/`max_5y`, `volatility`, and `trend` (improving/stable/declining).

### 5.2 GuV presence (explicit rule)
A company may file a GuV in some years and not others (small firms often file Bilanz only; larger ones must add a GuV). Therefore:
- GuV is stored as a **multi-year time series** (per line item, by year) — never "just one year."
- For filtering, the record carries **rollup flags**: `has_guv` (GuV in **any** year), `has_guv_latest` (GuV in the **most recent** filing — the practical "currently reports a GuV"), and `guv_years` (list of years with a GuV).
- The MCP lets users **filter on both** `has_guv` and `has_guv_latest`.

### 5.3 Growth horizons (explicit rule)
- **Absolute values** (Bilanz/GuV figures, revenue): per-year YoY plus **1-year, 3-year, and 5-year** growth (3y/5y as CAGR), plus average, volatility, min/max. **2-year and 4-year are intentionally excluded** (low signal, more clutter), but the horizon set is **configurable** so they can be switched on later without a redesign.
- **Ratios** (percentages): tracked via per-year change, rolling averages (3y/5y), min/max, volatility, and trend — **not** CAGR (a CAGR of a percentage is not meaningful).

### 5.4 Size & peers
Größenklasse (`gkl`) is taken directly from the API. Peer percentiles rank a company within its cohort (by size and, later, sector) and are computed across the whole universe.

### 5.5 WERT_TSD scaling
Some filings report values in thousands (`WERT_TSD = j`). These must be scaled ×1000 during parsing. Silent failure here corrupts all downstream numbers, so it is an explicit, tested rule.

### 5.6 Incremental updates (no data loss)
When a new Jahresabschluss (or register change) arrives:
- The company's consolidated record is **rebuilt from all its filings**, so the new year is appended to every time series and all growth/CAGR/trend values are recomputed; **no prior year is ever lost**.
- The MCP serves the updated record on the next query automatically.
- Every rebuild is **versioned** and references the previous version (see §8).

---

## 6. The MCP product (functional)

### 6.1 What users can do (Version 1 tools)
- **search_companies** — filter the universe by **company name (substring)**, legal form, Bundesland, Größenklasse, financial ranges (Bilanzsumme, Eigenkapitalquote, revenue, employees), growth profile, **has_guv / has_guv_latest**, last filing year, and **primary Geschäftsführer current age** (succession-window screen); sorted (bilanzsumme, revenue, equity_ratio, employees, last_filing_year) and paginated.

> **Served scope (v1):** ingest, raw archival and consolidation cover the **whole company universe (all Rechtsformen)**. The **served `10_presentation` layer is GmbH-first** — the Initial Load runs `backfill-process` with `PROCESS_RECHTSFORMEN=GES` (~213k GmbHs). Other forms (KG, AG, OG, GEN, SE, …) are fully pipeline-capable and are served once that env is widened; until then `search_companies` results are GmbH only.
- **get_company_details** — the full consolidated record for one company.
- **get_company_history** — the financial time series for a company.
- **get_document** — the stored original Jahresabschluss (PDF or XML) or a link to it, for **any** filing including PDF-only ones.
- **list_sectors** — Version 1 returns a legal-form + size-class taxonomy with counts (named generically so a real sector taxonomy can replace it later).
- **get_cohort_summary** — distribution statistics for a cohort (by legal form / Bundesland / size).
- *(optional)* **find_peers** — nearest companies by size, region, and financial profile.

### 6.2 Access model
- **Signup by email → API token.** Minimal web surface only (no marketing site in v1).
- **Free tier with rate limits** (per-minute and per-day) to keep bots out. Designed so a **paid tier** is a configuration change later, not a rewrite. Usage is metered.
- Every response carries the **CC BY 4.0 attribution** to the Firmenbuch source.

### 6.3 Coverage dashboard (internal)
An internal dashboard reports, across the universe: how many companies have ≥1 structured-XML filing, how many are **PDF-only**, and how many have no filing at all; plus parse-success rate by format and by year. This makes the size of the PDF-only gap visible and tracked over time, and informs whether OCR is worth adding later.

---

## 6.4 Initial load vs. ongoing operation (two separate regimes)

The system has **two clearly separated modes of running** (detailed technically in the Technische Spezifikation §15a):

- **Initial Load (one-off bootstrap, run once by hand):** build the complete **company registry** of all ~640k legal entities (seeded from the official data.gv.at HVD bulk dataset), download every available Jahresabschluss to storage, then process all layers so the MCP goes live. Expected duration: **hours if the bulk dataset includes the documents; otherwise ~1–3 days**, dominated by downloading.
- **Operation (daily steady state, automatic):** **once per day**, the system checks only what **changed** (new filings, new companies, register changes), updates just those companies through the full pipeline, and the MCP reflects them. Typical daily runtime: **minutes**. New companies are picked up automatically in the same daily run.

The **company registry** is a single authoritative list of every company that **lives in our own store** and drives every operation. Completeness is guaranteed by: seeding from the authoritative full dataset, adding new registrations daily, and periodically reconciling against the authoritative dataset. Daily runs are **non-overlapping** (a run never starts while the previous one is still going) and the daily schedule is enabled only after the Initial Load completes, so the two regimes never collide.

**Active and inactive companies are both kept.** Each company carries a lifecycle `status` (active / historical / **deleted** = gelöscht). The registry is the **single source of truth** for status; it is refreshed by the periodic authoritative reconciliation and (where available) by the daily register-change feed, and a status change (e.g. a Löschung) propagates to the queryable data even without a new Jahresabschluss. The MCP lets users filter by status (active, inactive, or all — default **all**), so both currently-registered and dissolved companies are searchable.

---

## 7. Compliance & licensing (functional constraints)

- **Attribution (CC BY 4.0).** The MCP and any web surface must display a visible source credit (e.g. *"Quelle: Österreichisches Firmenbuch / BMJ — Justiz, CC BY 4.0"*).
- **Use the API, never scrape the portal.** Bulk access is via the official HVD API only; scraping the JustizOnline web UI is prohibited.
- **GDPR for personal data.** Geschäftsführer and signatories are natural persons. **Posture for Version 1:** company and financial data are re-served freely; the **name is withheld** by default (single toggle, opened only once a lawful basis is documented). We **do** expose the **age at signing** and the **birth year (year only — never the month, day, or name)**, plus signatory count and stability — these are the core succession signals and, without a name, are a strong data-minimization. Note: birth-date coverage in the source is **partial**, so age/birth-year is present only for a subset of companies. *(Year-of-birth-without-name is defensible minimization; still worth a short legal confirmation before public launch.)*
- No fees apply to the HVD data; running cost is infrastructure only.

*(This states licensing/compliance facts, not legal advice; a short confirmation from an Austrian lawyer on the GDPR basis is advisable before public launch.)*

---

## 8. Data lineage & provenance (functional requirement)

Every record, at every pipeline stage, must be **traceable, verifiable, timestamped, and versioned**:
- A **unique id** per document at each stage, with each downstream document **referencing the upstream id** — so the full chain (raw → parsed → consolidated → derived → presented) is walkable.
- A **content hash** per document for integrity and change detection.
- **Timestamps for each processing step**, in `2026-06-16T17:30:00Z` format, accumulated through the chain.
- **Field-source tracking:** where a field is renamed from the source (the parsing step), the source field is recorded.
- **Versioning of consolidated records:** when a new filing arrives, the rebuilt record references the **previous version's hash**, and a version counter increments — so history of changes is auditable and nothing is silently lost.
- **Data-quality checks** recorded as part of provenance (e.g. Aktiva = Passiva, prior-year reconciliation, WERT_TSD applied).
- Public MCP responses expose a **trimmed provenance** (source, licence/attribution, data version, build time, document links) — not the internal hash chain.

*(Concrete field-level design and sample documents are in the* Technische Spezifikation *and the* Pipeline Step Samples *artifact.)*

---

## 9. Non-functional expectations (functional level)
- **Freshness:** data updates on a **daily** cadence (filings publish slowly; daily latency is acceptable).
- **Reliability / no loss:** the pipeline must be **replayable** and must not lose records on failure; the original raw filings are kept immutably and everything downstream is rebuildable from them.
- **Modularity & extensibility:** each stage is independent; **new data sources and a scoring layer can be added later** without reworking the core. (Reserved layers and seams are defined technically.)
- **Cost-consciousness:** HVD data is free; infrastructure runs cheaply (scale-to-zero, serverless, incremental processing).
- **Self-contained & documented deliverable:** a single, **closed project** that builds and runs with **no dependency on any external project** or anything outside its own folder. The repo uses an intuitive, scalable structure with a **README in every major subfolder**, and the READMEs are **inter-navigable** (each links up to the root and across to the neighbouring pipeline stages).

---

## 10. Open items (to confirm; none block building)
1. **Tier capabilities** — confirm against the key: does `auszug` work (drives master-data availability), and do the **change feeds** work (drives the daily-update design)? The Technische Spezifikation specifies behavior for **both** answers, so neither blocks the build.
2. **Enumeration source** — confirm what the `data.gv.at` HVD bulk dataset contains (full company list, and whether it includes the Jahresabschlüsse). Determines whether the initial load is a bulk download or an API crawl; both are specified.
3. **GDPR basis** for exposing officer names publicly — document it, and confirm the personal-data gating default.
4. **HVD API rate-limit / fair-use** ceiling for the full ~200k sweep and daily deltas.
5. **Two live sample files** to finalize field mapping: one real `auszug` (Kurzinformation) response (if the tier returns it) and one real **JAb 4.0** XML filing.

---

## 11. Glossary (selected Fachbegriffe)
- **Firmenbuch** — Austrian company register. **FNR / Firmenbuchnummer** — company id (e.g. `093450b`).
- **Jahresabschluss** — annual financial statement; **Konzernabschluss** — consolidated group statement.
- **Bilanz** — balance sheet; **GuV** — Gewinn- und Verlustrechnung (P&L).
- **Eigenkapital** — equity; **Eigenkapitalquote** — equity ratio; **Verbindlichkeiten** — liabilities; **Rückstellungen** — provisions; **Anlagevermögen / Umlaufvermögen** — fixed / current assets.
- **Rohergebnis / Umsatzerlöse** — gross result / revenue; **Jahresüberschuss** — net income.
- **Größenklasse (GKL)** — size class (K/M/G). **Rechtsform** — legal form (GmbH, AG, …). **Sitz** — registered seat. **Geschäftszweig** — business purpose. **Geschäftsführer** — managing director. **Stammkapital** — share capital.
- **Größenklasse / WERT_TSD** — values-in-thousands flag. **Stichtag** — balance-sheet date.
- **HVD** — High Value Datasets (the free EU open-data channel). **Verrechnungsstelle** — the (paid, legacy) Firmenbuch billing intermediary — *not used* here; we use the free HVD channel.
