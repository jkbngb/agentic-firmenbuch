# Banks & Insurers — Specification & Build Plan (V1 research, V2 implementation)

> Status: **research + design**, not yet implemented. This document is
> the build brief for the next iteration. Three companion research reports live in
> [`docs/research/`](research/):
> [`jab40_bank_insurer_support.md`](research/jab40_bank_insurer_support.md) ·
> [`banks_BWG_schema.md`](research/banks_BWG_schema.md) ·
> [`insurers_VAG_schema.md`](research/insurers_VAG_schema.md).
>
> Read those for citations. This file is the actionable plan distilled from them
> plus the live-data audit performed on 2026-06-27.

---

## 0. TL;DR

1. **Banks (BWG) and insurers (VAG) cannot be served structurally from the
   Firmenbuch alone, ever.** JAb 4.0 is UGB-only by design; the official BMJ
   change-log explicitly says BWG/VAG support is a future, unscheduled plan.
   Banks and insurers file their Jahresabschluss as **PDF only** in the
   Firmenbuch (and listed entities additionally as ESEF/iXBRL zips).
2. **Our pipeline is not broken** by this — the parser correctly produces
   `has_bilanz=False` PDF-only stubs and never silently mis-maps a bank/insurer
   into UGB positions. We simply have ~0 financial data for them today.
3. **The fix is a two-track plan**, not a parser bug fix:
   - **Track A (cheap, ~3 days):** detect "this is a bank / insurer" from
     master-data + external registers (OeNB / ECB / FMA) and surface a
     `is_financial_institution` + `fi_kind` flag on every served record.
     Add a "ratios do not apply" caveat in tool output. **No financials yet, but no
     more silent confusion.**
   - **Track B (proper, several weeks):** ingest from the right structured
     sources — **EBA Pillar 3 Data Hub** for bank prudential data; **SFCR/QRTs**
     for insurer solvency data; PDF/ESEF extraction for the Firmenbuch JA itself
     (BWG Anlage 1/2 and VAG §§ 144/146 positions). Parallel BWG and VAG position
     taxonomies, parallel ratio blocks, a new `fi_financials` and `fi_ratios`
     section in the served record.
4. **The `is_financial_institution` flag should be added even before any structured
   financials.** It's the single most important thing — it lets the MCP server
   tell a downstream agent "do not apply EBIT-margin reasoning to this entity",
   which is the failure mode we want to prevent.

---

## 1. Current state (audited 2026-06-27)

### 1.1 Registry has all the entities — financials are empty

Sample queries against `10_presentation` for the largest Austrian banks and
insurers:

| Entity | FN | n_filings | Bilanz positions | GuV positions |
|---|---|---:|---:|---:|
| Erste Group Bank AG | `033209m` | 0 | 0 | 0 |
| Raiffeisen Bank International AG | `122119m` | 0 | 0 | 0 |
| VIENNA INSURANCE GROUP AG | `075687f` | 0 | 0 | 0 |
| WIENER STÄDTISCHE VERSICHERUNG AG | `333376i` | 0 | 0 | 0 |
| DONAU Versicherung AG | `032002m` | 0 | 0 | 0 |
| Generali Versicherung AG | `038641a` | 1 (2007) | 0 | 0 |
| Allianz Elementar Vers.-AG | `034004g` | 0 | 0 | 0 |

For comparison, **non-financial subsidiaries** of those same groups (Bank Austria
Real Invest, UNIQA Real Estate, BAWAG P.S.K. Immobilien — all GmbH that file
plain UGB) **do appear** with full 15-position Bilanz time series. So the gap is
not at the registry layer; it is structural to the JA format the parent FI
files.

Heuristic name-pattern counts in `10_presentation` (rough — includes ancillary
subsidiaries, leasing arms, payment processors):

| Name pattern | Count |
|---|---:|
| `…BANK…` | 484 |
| `RAIFFEISEN` | 577 |
| `VOLKSBANK` | 23 |
| `SPARKASSE` | 135 |
| `HYPO ` | 45 |
| `VERSICHERUNG` | 1,219 |
| `PENSIONSKASSE` | 6 |

Most of those are non-bank, non-insurer entities that just happen to have the
keyword in their name (e.g. service-arm GmbHs). The real population (see §3.1
and §3.2) is **~440-460 banks (Hauptanstalten)** and **~75-95 insurers** in
Austria.

### 1.2 Raw blobs: PDF, ESEF/iXBRL ZIP, occasional legacy XML wrapper

Inspecting Blob `90-raw/`:

- **RBI** (`122119m/`): 20 yearly filings 2005-2024. **All PDF**, except
  `2024-12-31` which is a **ZIP containing ESEF/iXBRL** (taxonomyPackage.xml,
  `*.xsd`, `*_cal.xml`, `*_def.xml`, `*_lab-de.xml` — the standard ESEF Annex
  IV layout) plus a reportPackage PDF. ESEF is the EU-mandated public-company
  format for listed issuers since FY 2020.
- **VIG** (`075687f/`): 2 PDF filings (2008, 2009). Sparse — the Konzern's main
  filings are at the operating subsidiaries.
- **Generali** (`038641a/`): 1 XML from 2007. Inspecting it: `<HGBFORM>JABPDF</HGBFORM>`,
  `<GRUND>VERS</GRUND>` — the XML is **a metadata wrapper, the actual JA is the
  attached PDF**. No Bilanz/GuV positions inside the XML, by design.
- **Erste Group Bank** (`033209m/`): master `auszug.json` only — no filings at
  all in the raw container, likely because no XML was ever attached. (PDF-only
  attachments may live elsewhere on JustizOnline.)

### 1.3 What our parser does with this today

Confirmed by reading [`packages/70_parse/src/fbl_parse/parser.py`](../packages/70_parse/src/fbl_parse/parser.py)
and [`variant.py`](../packages/70_parse/src/fbl_parse/variant.py):

- PDF-only filings → `parse_pdf_only()` → `has_bilanz=False`, document link
  preserved, no positions extracted. **No data, no error.**
- Legacy XML with `<HGBFORM>JABPDF</HGBFORM>` → parsed as `legacy_finanzonline`,
  yields zero positions (because there are zero numeric elements in the XML —
  they're in the PDF). The §15b-2 zero-positions guardrail would normally
  dead-letter this, but it currently does not for pre-JAb 4.0 variants (worth
  confirming and tightening).
- ESEF ZIP → **not parsed at all.** Today, ingest does not unzip. The ZIP just
  sits in blob.

**Bottom line: there is no silent mis-mapping risk today. Banks/insurers fall
through cleanly as "no structured data". The user-visible problem is the gap,
not corrupted data.**

---

## 2. Why this is structurally hard

Three things compound to make BWG/VAG data fundamentally different from UGB
data — not just "different numbers" but "different schema, different sources,
different cadence".

### 2.1 Different accounting law

- **UGB** §§ 198-243 + Formblatt-VO (Anlagen 1/2/3) → what JAb 4.0 encodes.
  Almost all Austrian companies file under this.
- **BWG** §§ 43-58 + **BWG Anlage 1** (Bilanz layout) + **BWG Anlage 2** (GuV
  layout) → the only valid schema for banks (Kreditinstitute). Position
  ordering, names, and aggregation rules are completely different — no "EBIT",
  no "Umsatzerlöse", instead Nettozinsertrag → Provisionsergebnis →
  Handelsergebnis → Betriebsergebnis → Wertberichtigungen.
- **VAG 2016** §§ 136-167, with the bilanz layout in **§ 144** and the GuV
  layout in **§ 146**, further detailed by the FMA's **VU-RLV** (BGBl. II
  316/2015). Three parallel technical accounts (Leben I / Sach II / Kranken III)
  plus a non-technical IV. Composite insurers are barred (§ 8 Abs. 4 VAG) so a
  given AG runs one Sparte only.

### 2.2 Different filing format

JAb 4.0 (the XML schema BMJ enforces from 2026 forward) is exclusively the UGB
position tree. Banks and insurers were **explicitly excluded** during JAb 4.0
drafting. Result: they file **PDF** at the Firmenbuch (often with a stub XML
that just sets `HGBFORM=JABPDF` and `GRUND=BANK` or `GRUND=VERS`). For listed
banks/insurers there is additionally the **ESEF / iXBRL ZIP** required by the
EU Transparency Directive — that *is* machine-readable but uses each issuer's
own taxonomy, not a uniform one.

### 2.3 The "real" structured data lives elsewhere

For banks: **EBA Pillar 3 Data Hub** (P3DH) — operational from 2025 for large &
other institutions, 2026 for SNCIs. XBRL-tagged disclosures including
CET1/Tier 1/Total capital ratios, RWA breakdowns, NPL ratios, LCR, NSFR,
leverage ratio. Per institution, per quarter or per year, EU-wide harmonised.
**This is the right source for bank prudential metrics.**

For insurers: **SFCR (Solvency and Financial Condition Report)** + **public
QRTs** (Implementing Regulation (EU) 2023/895, Annex I templates) — published
annually by every solo entity and every group. The QRTs are in standardised
cell IDs (S.02.01 BS, S.05.01/02 P&L by LoB, S.12 / S.17 technical provisions,
S.19 dev triangles, S.22 LTG, S.23 own funds, S.25 SCR, S.28 MCR). EIOPA
publishes aggregates but does not run a free per-insurer download portal —
each insurer publishes its own SFCR PDF on its IR page, the QRTs are embedded.

---

## 3. Detection: who is a bank / insurer?

This is **Track A** — pure metadata work, no parser changes needed.

### 3.1 Bank detection

In strictly decreasing reliability:

| Signal | Authoritative | Notes |
|---|---|---|
| **OeNB Bankstellenverzeichnis** | ✓ | Daily CSV. Has BLZ ↔ Firmenbuch-Nr columns. Hauptanstalten ≈ 440-460. |
| **FMA Konzessionsregister "Bankenkonzessionen"** | ✓ | HTML + downloadable. Lists every concessioned KI. Cross-check vs OeNB. |
| **ECB SSM list** of supervised institutions | partial | Only LSI/SI under direct ECB sup. — small subset. |
| ÖNACE 64.19 / 64.92 / 66.11 / 66.19 | weak | ÖNACE is **not** stored in the Firmenbuch record today. Would need a second source. |
| Name regex `Bank|Sparkasse|Raiffeisen|Volksbank|Hypo|Bausparkasse` with exclusions | last-resort | High recall, lots of false positives (leasing arms, IT subsidiaries). |
| Rechtsform `eGen` (e.g. Raiffeisen-Lagerhaus is **not** a bank but most local Raiffeisen banks are) | adversarial | Use only after positive name+BLZ match. |

**Implementation**: ingest the OeNB CSV nightly, write to a new
`00_directories/banks` container (FN → BLZ, license number, license date,
license type "Kreditinstitut" / "Spezialkreditinstitut" / "E-Geld" /
"Zahlungsinstitut"), join on FN at the consolidate stage.

### 3.2 Insurer detection

| Signal | Authoritative | Notes |
|---|---|---|
| **FMA Konzessionsregister "Versicherungsunternehmen"** | ✓ | HTML, downloadable. Lists every VU + VVaG with concession scope (Leben/Sach/Kranken/Re). |
| **ECB / Banco de España "Register of Insurance Corporations"** | ✓ | EU-wide CSV, ~130 AT entries with LEI. Use GLEIF to map LEI → Firmenbuch-Nr. |
| ÖNACE 65.11 / 65.12 / 65.20 (Life / Non-life / Re) | weak | ÖNACE not on Firmenbuch record. |
| Rechtsform `VER` (Versicherungsverein auf Gegenseitigkeit) | ✓ | Strong positive signal (every VVaG is a Versicherer). |
| Name regex `Versicherung\|Vers\\.[ -]AG\|Reinsurance\|Rückversicherung` with broker exclusion | last-resort | "X Versicherungsmakler GmbH" is a broker, not a VU — must exclude. |

**Implementation**: same pattern — pull FMA list nightly, write to
`00_directories/insurers`, join on FN at consolidate.

Estimated AT-domiciled insurer count: ~75-95.

### 3.3 The flag

Add to `PresentedIdentity` (and propagate to the search card):

```python
class PresentedIdentity(BaseModel):
    fnr: str
    register_id: str
    name: str
    legal_form: str
    status: str
    court: str | None
    # NEW:
    is_financial_institution: bool = False    # bank OR insurer OR pensionskasse
    fi_kind: Literal["bank", "insurer", "pensionskasse", "investmentfirm"] | None = None
    fi_license_authority: str | None = None   # "FMA" mostly
    fi_license_number: str | None = None      # OeNB BLZ for banks, FMA license # for insurers
```

The flag is set even when we have **zero financial data** for the entity. That
is the whole point — it tells the agent "don't reason about this with UGB-shaped
ratios".

The MCP `search_companies` card adds two columns: `is_financial_institution`
(bool), `fi_kind` (string). The `get_company_details` output adds a clear
caveat at the top of the `financials` object when the flag is true.

---

## 4. Financial data: where to get it (Track B)

### 4.1 Banks

Three sources, strongest first:

1. **EBA Pillar 3 Data Hub** (P3DH) — *the* right source for prudential data:
   - CET1 ratio, T1 ratio, Total capital ratio (Pillar 1)
   - RWA total + breakdown (credit / market / operational)
   - NPL ratio, NPL coverage
   - LCR, NSFR, leverage ratio
   - per institution, quarterly or annual, XBRL-tagged
   - Free, EU-wide harmonised, machine-readable
2. **ESEF/iXBRL ZIPs** in our `90-raw` already (for listed banks like RBI):
   - Full IFRS Bilanz + GuV at issuer-specific taxonomy
   - Use [Arelle](https://arelle.org) or [python-edgar](https://github.com/dgunning/edgartools) for XBRL extraction
   - Issuer-specific taxonomy means we'd need per-bank concept mapping for full
     comparability — not trivial.
3. **Firmenbuch PDF** (legal mandatory JA):
   - BWG Anlage 1/2 positions (UGB-incompatible)
   - Requires layout-aware PDF extraction (Azure Document Intelligence,
     Reducto, LlamaParse, or a vision LLM with a templated prompt)
   - Highest cost, lowest harmonisation across banks (each bank has its own
     PDF formatting)

**Recommended build order:** P3DH first (machine-readable, lowest effort,
highest user value — that's what investors actually look at). ESEF second
(decent ROI for the largest banks). PDF last and only if specific UGB-comparable
numbers are needed for niche use cases.

### 4.2 Insurers

Same three-source pattern, weighted differently:

1. **SFCR public QRTs** — *the* right source:
   - S.02.01 Bilanz (full balance sheet, harmonised)
   - S.05.01/02 P&L by line of business
   - S.12, S.17 Technical provisions (life / non-life)
   - S.19 development triangles (non-life claims runoff)
   - S.22, S.23 own funds
   - S.25, S.28 SCR / MCR
   - Per solo entity AND per group, annually, ~13 standardised templates,
     embedded in the SFCR PDF (you find the PDF on the insurer's IR page)
2. **Firmenbuch PDF** (legal mandatory JA):
   - VAG § 144 Bilanz / § 146 GuV layout
   - Three parallel technical accounts (Leben/Sach/Kranken/Re) + non-technical
   - Same PDF-extraction tech as banks
3. **EIOPA aggregates** — EU-wide solo/group statistics, useful for context but
   not for per-entity profiles.

### 4.3 ESEF detection while we're at it

While building the bank/insurer track, also handle the ESEF/iXBRL ZIP case
*generally* — any listed Austrian company (not just banks) will start filing
ESEF ZIPs in `90-raw`. Today we ignore them. Add an `esef` filing variant to
the parser dispatch and route via Arelle. This is value beyond the FI use case.

---

## 5. Pipeline design

### 5.1 New / changed components

| Layer | New / change |
|---|---|
| `00_directories` (new container) | `banks` + `insurers` lookup. Nightly pull from OeNB CSV + FMA Konzessionsregister + ECB IC list + GLEIF (for LEI mapping). Schema: `{fnr, blz?, fma_license, ec_lei?, name, legal_form, sparte?, license_type, license_date, source, last_seen_at}`. |
| `90_ingest` | New variants in raw download: `.zip` (ESEF), continue to keep `.pdf`. |
| `70_parse` | New parsers: `esef_xbrl` (Arelle-based, IFRS taxonomy), `bwg_pdf` and `vag_pdf` (Document Intelligence / vision-LLM). Add variant detection in `variant.py`. |
| `core/mapping` | Two new taxonomies parallel to the 317-entry UGB tree: `bwg_positions.json` (Anlage 1 + Anlage 2), `vag_positions.json` (§ 144 + § 146 incl. all three technical accounts). |
| `50_consolidate` | Recognise FI filings: keep them in a separate `fi_financials` block of `ConsolidatedCompany`, never mix with UGB `financials`. Set `identity.is_financial_institution` from `00_directories` join. |
| `30_derive` | New ratio computer for banks (`bwg_ratios.py`) and insurers (`vag_ratios.py`). Emit `fi_ratios` block. Do **not** emit UGB ratios for FI entities (zero or null them out). |
| `10_present` | Pass through both blocks. Add identity flags. Add caveat string in `financials` object: `caveat: "ratios_not_comparable"` when `fi_kind` is set. |

### 5.2 No regression for current 341k UGB entities

All of the above is **purely additive**. UGB-filed entities continue to flow
through exactly the same way. The branch happens in `50_consolidate` after
the directory join sets `is_financial_institution`:

```
                                                ┌── UGB path (existing) ── financials, ratios
filings ── parse ── consolidate ── directory ──┤
                                                └── FI path (new) ── fi_financials, fi_ratios
```

### 5.3 Storage cost

`00_directories` is tiny (~600 docs total). The new parser variants will
multiply per-filing parse cost only for the small FI population (~500
entities), so storage and Cosmos RU impact is negligible.

---

## 6. MCP server changes

### 6.1 New fields in served records

```jsonc
// In identity (every record):
"is_financial_institution": true,
"fi_kind": "bank",
"fi_license_authority": "FMA",
"fi_license_number": "...",   // OeNB BLZ for banks, FMA-Konz# for insurers

// In financials, when fi_kind is set:
"financials": {
  "caveat": "Banks (BWG) / insurers (VAG) use a different statement schema than UGB. UGB-shaped ratios (EBIT, EBIT margin, current ratio) are intentionally null. See fi_financials and fi_ratios.",
  // ... (existing UGB fields all null)
},

// New top-level block, only populated for FI entities:
"fi_financials": {
  "schema": "BWG_Anlage_1_2",   // or "VAG_S144_S146"
  "latest_year": 2024,
  "source": "p3dh" | "esef" | "sfcr" | "pdf_extracted",
  "positions": { /* schema-specific position name → value */ }
},
"fi_ratios": {
  // Banks: { "cet1_ratio": 0.142, "nim": 0.018, "cost_income": 0.55, "npl_ratio": 0.021, ... }
  // Insurers: { "combined_ratio": 0.94, "loss_ratio": 0.65, "expense_ratio": 0.29, "sii_ratio": 1.85, ... }
}
```

### 6.2 New search filters

Add to `SearchFilters`:

- `is_financial_institution: bool | None`
- `fi_kind: Literal["bank","insurer","pensionskasse","investmentfirm"] | None`
- `bank_cet1_min: float | None`        (banks only)
- `insurer_combined_ratio_max: float | None`  (insurers only)
- `insurer_sii_ratio_min: float | None`

### 6.3 Tool changes

| Tool | Change |
|---|---|
| `search_companies` | Add the new filters above. Result card includes `is_financial_institution`, `fi_kind`. |
| `get_company_details` | Include `fi_financials` + `fi_ratios` blocks when present. Always include the `financials.caveat` for FI entities. |
| `get_full_record` | Same + the raw bank/insurer position taxonomy. |
| `describe_fields` | Document the new fields, schemas, and caveat. Explain when fi_ratios are computable from Firmenbuch alone vs. when they need P3DH/SFCR. |
| `list_sectors` | Add a `financial_institutions` group with sub-types. |
| `get_coverage` | New axis: "of N detected banks/insurers, M have structured financials, K have only the flag". |

### 6.4 New tools (optional, V3)

- `find_banks(...)` — convenience wrapper, filters by `fi_kind=bank`
- `find_insurers(...)` — same for insurers
- `get_bank_prudential(fnr)` — direct CET1/RWA/NPL fetch from `fi_financials`

These are sugar; the existing tools with the new filter set are sufficient
for V2.

### 6.5 Backwards compatibility

- Existing tool signatures unchanged.
- New fields are additive — old MCP clients see them as extra keys they
  can ignore.
- A 341k-entity UGB query returns identical results before/after the change.

---

## 7. Ratios — what to compute and when

### 7.1 Banks

| Ratio | Computable from | Notes |
|---|---|---|
| Net Interest Margin (NIM) | BWG Anlage 2 | Nettozinsertrag / Ø Bilanzsumme |
| Cost / Income Ratio | BWG Anlage 2 | (Personalaufwand + Sachaufwand) / Betriebsertrag |
| Loan / Deposit Ratio | BWG Anlage 1 | Forderungen an Kunden / Verbindlichkeiten ggü. Kunden |
| Return on Equity (ROE) | BWG Anlage 1 + 2 | Jahresergebnis / Ø Eigenkapital |
| Risk-Cost Ratio | BWG Anlage 2 | Risikokosten / Ø Kreditportfolio |
| **CET1 Ratio** | **EBA P3DH** | Not in any JA — Pillar-1 capital reporting |
| **RWA** | **EBA P3DH** | Same |
| **NPL Ratio** | **EBA P3DH** | (Risk-cost approximates it from BWG, but the EBA number is the comparable one) |
| **LCR / NSFR** | **EBA P3DH** | Liquidity ratios, prudential reporting only |
| **Leverage Ratio** | **EBA P3DH** | Basel III non-risk leverage |

### 7.2 Insurers

| Ratio | Computable from | Notes |
|---|---|---|
| Combined Ratio (Schaden-Kosten-Quote) | VAG § 146 | (Schadenaufwand + Aufw. Vers.-Betrieb) / verrechnete Prämien |
| Loss Ratio (Schadenquote) | VAG § 146 | Schadenaufwand / verrechnete Prämien |
| Expense Ratio (Kostenquote) | VAG § 146 | Aufwendungen Vers.-Betrieb / verrechnete Prämien |
| ROE | VAG § 144 + § 146 | Jahresergebnis / Ø Eigenkapital |
| Kapitalanlagenrendite | VAG § 144 + § 146 | Kapitalanlageergebnis / Ø Kapitalanlagen |
| **SCR Coverage Ratio** | **SFCR S.25** | Eligible own funds / SCR |
| **MCR Coverage Ratio** | **SFCR S.28** | Eligible own funds / MCR |
| **Own Funds Tiering** | **SFCR S.23** | Split T1 / T2 / T3 own funds |
| **Technical-Provision Run-off Triangles** | **SFCR S.19** | Non-life only |

The "computable from VAG / BWG" ratios are achievable from a PDF extraction
alone. The Solvency II / P3DH ratios need a second pipeline.

### 7.3 UGB ratios stay null for FI entities

`equity_ratio` is the only existing UGB ratio that arguably makes sense for FI
entities (capital / total assets). For consistency and to avoid confusing
comparison, **all 13 UGB ratios should be null** when `fi_kind` is set, and the
FI-specific block is the only ratio source. The `caveat` string in `financials`
explains this.

---

## 8. Recommended build order

### Phase 1 — Detection (Track A) · est. 3 days

1. Implement `00_directories/banks` ingest: download OeNB
   Bankstellenverzeichnis CSV nightly, parse, write per-bank docs keyed by
   FN.
2. Same for `00_directories/insurers`: FMA Konzessionsregister + ECB IC list +
   GLEIF for LEI mapping.
3. `50_consolidate`: join on FN, set `identity.is_financial_institution` /
   `fi_kind` / `fi_license_*`. Set `financials.caveat` when flag is true.
4. `10_present`: pass through the new identity fields.
5. MCP `search_companies`: include the flag in the result card; add the new
   filter parameters.
6. MCP `get_company_details`: emit `financials.caveat` when present.
7. `describe_fields`: document the new fields.

**No new parser, no new ratio computer.** This phase alone delivers most of
the user value (agents stop trying to apply UGB reasoning to banks).

### Phase 2 — ESEF / iXBRL ingestion · est. 1 week

8. Variant detector recognises `.zip` files in `90-raw`.
9. Arelle-based parser extracts IFRS positions from ESEF (issuer-specific
   taxonomy, mapped via concept-anchoring).
10. Write to `fi_financials` block with `schema="esef_ifrs"`.
11. Initially benefits only listed banks/insurers (~12 entities in AT) but the
    parser is reusable for any listed company.

### Phase 3 — EBA Pillar 3 Data Hub for banks · est. 1 week

12. Ingest from P3DH XBRL API per bank, per quarter.
13. Write to `fi_financials` with `schema="bwg_prudential"`.
14. Compute `fi_ratios.{cet1_ratio, npl_ratio, lcr, nsfr, leverage_ratio, ...}`.

### Phase 4 — SFCR / QRT for insurers · est. 1-2 weeks

15. Crawl each insurer's IR page for the annual SFCR PDF.
16. Extract the embedded QRTs (S.02, S.05, S.12, S.17, S.22, S.23, S.25, S.28).
17. Write to `fi_financials` with `schema="sfcr_qrt"`.
18. Compute `fi_ratios.{combined_ratio, loss_ratio, expense_ratio, sii_ratio,
    mcr_ratio, ...}`.

### Phase 5 — Firmenbuch BWG/VAG PDF extraction · est. 2-3 weeks

19. Document Intelligence / vision-LLM templated extraction for the
    Firmenbuch-PDF JA, per BWG Anlage 1+2 and VAG § 144+146 schemas.
20. Per-bank / per-insurer position mapping in `core/mapping/bwg_positions.json`
    and `core/mapping/vag_positions.json`.
21. Write to `fi_financials` with `schema="bwg_pdf_extracted"` or
    `schema="vag_pdf_extracted"`.
22. Bank/insurer-specific Bilanz time series, comparable across the population.

**Phase 1 + 2 + 3 = MCP can answer real bank questions in ~3 weeks. Phase 4
adds insurers. Phase 5 unlocks the bilanz time series for the historical
record.**

---

## 9. Flag policy — final decision

**Yes, add `is_financial_institution: bool` and `fi_kind: enum` on every
served record, set from external directories, regardless of financial-data
availability.**

Rationale:

1. **It prevents the worst failure mode**: an agent computing EBIT margin or
   equity ratio for a bank and reporting nonsense. The flag is the cheapest,
   most important signal.
2. **It's user-visible value with no financial data**: even with empty
   `fi_financials`, the user knows "this is a Versicherung, regulated under
   VAG, license-issued by FMA, license number XXX, license date YYYY".
3. **It future-proofs the schema**: by Phase 2/3/4 the same flag gates the
   `fi_financials` block. No client breakage.
4. **It enables proper search filtering immediately**: "find all Austrian
   banks with name containing 'Hypo'" works on day one of Phase 1.

A weaker alternative — leaving the flag out and only checking the legal form /
name — fails because:
- legal form `AG` doesn't distinguish bank vs non-bank
- legal form `VER` does (every VVaG is a Versicherer) but misses 90%+ of
  insurers that are AG
- name regex is high-recall, lots of false positives ("X Versicherungsmakler"
  is not a Versicherer)

The flag is the right abstraction.

---

## 10. Risks & open questions

1. **OeNB Bankstellenverzeichnis FN column completeness** — needs to be
   verified against ~20 known banks. If a few are missing FN, fall back to
   FMA Konzessionsregister or manual override file.
2. **FMA Konzessionsregister machine-readability** — currently HTML; may need
   a one-shot scraper. Worth asking FMA for a CSV export.
3. **ECB IC list ↔ GLEIF ↔ Firmenbuch join quality** — LEI mapping is
   imperfect, expect ~10% manual reconciliation needed for insurers.
4. **Per-issuer ESEF taxonomy normalisation** — RBI's `_def.xml` uses
   `rbinternational.com/20241231/...` concepts; comparing across issuers
   needs concept-anchoring to ESEF / IFRS Taxonomy core. Non-trivial but
   solved problem (Arelle handles it).
5. **PDF extraction quality** — Phase 5 is the hardest. Vision-LLM templated
   extraction from ~440 bank PDFs / year requires good prompt engineering
   and per-bank QA. Plan for 90% accuracy initially, manual review of the top
   20 banks until stable.
6. **Solvency II SCR scope (solo vs group)** — must record which entity level
   each SFCR refers to. Mixing group and solo SCRs would mislead.
7. **GDPR / commercial-license check** for P3DH and SFCR data — public
   regulatory disclosures, redistribution is allowed for non-commercial and
   commercial use under EU regulations. Verify before bulk-ingesting.

---

## 11. Effort summary

| Phase | Scope | Effort | Cumulative deliverable |
|---|---|---|---|
| 1 | Detection + flag + caveat | 3 d | Agents stop reasoning UGB-style on banks/insurers. Search by FI type works. |
| 2 | ESEF / iXBRL parser | 1 w | Listed banks/insurers get IFRS Bilanz/GuV time series. |
| 3 | EBA P3DH for banks | 1 w | CET1, NPL, LCR, leverage ratio for every AT bank. |
| 4 | SFCR / QRT for insurers | 1-2 w | Combined ratio, SII coverage for every AT insurer. |
| 5 | BWG/VAG PDF extraction | 2-3 w | Full BWG / VAG Bilanz time series from Firmenbuch PDF. |

**Phase 1 is the launch-essential one.** Even with zero structured financials,
the flag + caveat removes the biggest correctness risk. Phase 2-4 deliver real
financial data in the right schemas. Phase 5 is the last 20% — closes the gap
to "complete" but is the most engineering-intensive.

---

## 12. Reference research

- [`docs/research/jab40_bank_insurer_support.md`](research/jab40_bank_insurer_support.md) — confirmation that JAb 4.0 is UGB-only by design; what our parser does today; pragmatic recommendation.
- [`docs/research/banks_BWG_schema.md`](research/banks_BWG_schema.md) — legal basis (BWG §§ 43-58 + Anlagen 1/2); EBA P3DH as the structured source; OeNB Bankstellenverzeichnis for detection.
- [`docs/research/insurers_VAG_schema.md`](research/insurers_VAG_schema.md) — VAG 2016 §§ 136-167; § 144 Bilanz / § 146 GuV; SFCR + QRTs as structured source; ECB IC list + FMA Konz.-Register for detection.

End of plan.
