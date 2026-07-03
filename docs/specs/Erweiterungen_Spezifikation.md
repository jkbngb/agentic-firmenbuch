# Erweiterungen — Design & Build Spec (forward-looking)

> The detailed design record for the planned extensions, backing the
> [root `ROADMAP.md`](../../ROADMAP.md) (which is the lightweight, prioritised
> index). Each chapter is a self-contained, buildable work-package. Supporting
> deep-dive research (with external-source citations) lives in
> [`docs/research/`](../research/).
>
> **Naming convention:** the `*_Spezifikation.md` documents (Fachliche,
> Technische, Distribution, Rechtsform_Coverage) are the stable record of the
> **system as built**; this document is the **forward plan**. No version numbers
> in filenames — history is git. As a chapter ships, it moves from "planned" to
> "done" in `ROADMAP.md`.
>
> Status: **design + evidence**. Everything here is backed by a live audit of the
> production Azure data and a hands-on analysis of **64 real bank/insurer
> filings** pulled from the JustizOnline API on 2026-06-27 (into scratchpad, never
> written to Azure — production data untouched). **§8 (per-user usage metering)
> phase 1 is already shipped** (the `00_usage` container, `metered()` decorator
> and `get_my_usage` MCP tool are live); the remaining phases and all other
> chapters are not yet implemented.

## Chapters

1. [The two separate problems](#1-the-two-separate-problems) — ingest gap vs. schema
2. [Empirical evidence — what banks/insurers actually file](#2-empirical-evidence) (64-file analysis)
3. [Detection & the `is_financial_institution` flag](#3-detection--the-fi-flag)
4. [Banks — BWG schema handling](#4-banks--bwg-schema)
5. [Insurers — VAG schema handling](#5-insurers--vag-schema)
6. [Pipeline changes (all layers)](#6-pipeline-changes)
7. [Layer 10 + MCP server changes](#7-layer-10--mcp-server)
8. [Per-user usage metering](#8-per-user-usage-metering)
9. [Ingest-gap fix (the large-file download bug)](#9-ingest-gap-fix)
10. [Build order & effort](#10-build-order--effort)

---

## 1. The two separate problems

There are **two independent reasons** banks and insurers have no financial data
today. Conflating them was the original confusion. They need different fixes.

### Problem A — the ingest gap (general, not FI-specific)

We **have** collected all 642,588 companies' **identities** (name, FN, address,
management). Nothing is missing at the registry level. But the pipeline fetches
each company's *filings* in a separate, slower phase, and that phase is only
~32% done:

| Registry fact (live, 2026-06-27) | Count |
|---|---:|
| Total companies discovered | 642,588 |
| Filing-list ever fetched (`sucheUrkunde` ran) | 205,296 (32%) |
| **Never filing-checked** (`last_filing_check_at = null`) | **437,292 (68%)** |
| Pipeline state `failed` | 2,118 |
| Dead-lettered on document download | 5,830 (399 explicitly on `urkunde`) |

Most big banks/insurers (Erste, UniCredit, UNIQA Insurance, Wiener Städtische)
sit in the **never-filing-checked** bucket — discovered on 2026-06-20, the
filing-check phase simply hadn't reached them. A smaller group (RBI, VIG) **did**
get their filing list but the document *download* dead-lettered (see Problem A2).

This is the same reason ~437k *non-financial* companies also lack data. It is
**not** bank-specific. Fixing it is chapter [§9](#9-ingest-gap-fix).

#### Problem A2 — the large-file download bug

When the filing IS large (multi-MB bank/insurer PDFs), the `urkunde` download
fails: `dead_letter: "urkunde failed after N attempts: http 200"`. In our 64-file
sample pull, **40 of 104 attempts failed this way (38%)** — all on the largest
files. The "http 200" means the HTTP call returned 200 but the SOAP/base64 body
either truncated or failed to parse within the retry/timeout budget. This is
backlog item #4 and is detailed in [§9](#9-ingest-gap-fix).

### Problem B — the schema (genuinely FI-specific, the hard one)

Even once §9 is fixed and every bank/insurer PDF is downloaded, **we still
cannot extract structured Bilanz/GuV numbers from them**, because:

1. They file under a **different accounting law** (BWG for banks, VAG for
   insurers) — a completely different position tree than UGB §231.
2. They file as **PDF, not structured XML** — JAb 4.0 (the XML schema) is
   UGB-only by design and explicitly excludes banks/insurers.
3. **71% of those PDFs are scanned images** with no text layer (see §2) — so
   even PDF text extraction won't reach them without OCR.

Problem B is chapters [§3](#3-detection--the-fi-flag)–[§7](#7-layer-10--mcp-server).

**Both problems are real and both need solving. §9 (ingest gap) is generic and
cheap. §3-§7 (FI schema) is the substantial new work.**

---

## 2. Empirical evidence

Pulled **64 real filings** (104 attempted) for 13 major Austrian banks &
insurers directly from the live JustizOnline `urkunde` API. Hard findings:

### 2.1 Format reality

| Fact | Result |
|---|---|
| Filings downloaded OK | 64 (25 bank, 39 insurer) |
| **By file type** | **63 PDF, 1 ESEF/iXBRL ZIP** |
| Structured BWG/VAG XML | **0** — none exists |
| Download failures (large-file bug) | 40 of 104 (38%) |
| File size range | 0.2 MB – 7.4 MB, avg **4.0 MB** |

**Confirmed: operating banks/insurers file PDF only.** The single ZIP (RBI 2024)
is the EU-mandated ESEF/iXBRL filing required of *listed* issuers — additional
to, not instead of, the PDF.

### 2.2 The killer finding — 71% of PDFs are scanned images

Text-layer analysis across all 63 PDFs (chars extractable per page):

| PDF type | Count | Share | Implication |
|---|---:|---:|---|
| **Text layer** (directly extractable) | 18 | 29% | `pdftotext` / pdfplumber works |
| **Scanned image** (<200 chars/page) | 45 | **71%** | **OCR or vision-LLM required** |

Examples:
- BAWAG Group 2024 → text, 3,562 chars/page ✓
- UniCredit Bank Austria 2024 → text, 4,630 chars/page ✓
- Erste Group Bank 2024 → **scanned**, 7 chars/page (87 pages, 3 fonts) ✗
- Allianz Elementar (Leben + Sach) → **scanned**, 7 chars/page ✗
- Generali 2019 → **scanned**, 12 chars/page ✗
- Volksbank Wien 2018 → **scanned**, 6 chars/page ✗

### 2.3 The text-layer PDFs contain the full BWG/VAG schema

UniCredit Bank Austria 2024 (text layer) — extracted verbatim, the **BWG
Anlage 2 GuV** with real values:

```
 1. Zinsen und ähnliche Erträge                       5.188.307.302,30
 2. Zinsen und ähnliche Aufwendungen                 (3.580.890.470,25)
 3. Erträge aus Wertpapieren und Beteiligungen          150.115.927,15
 4. Provisionserträge                                   644.835.517,02
 5. Provisionsaufwendungen                              (94.512.538,61)
 9. Wertberichtigungen auf … (Aktivposten 9 und 10)
18. Steuern vom Einkommen und Ertrag                    (59.886.693,11)
```

And the **BWG Anlage 1 Bilanz** ("5. Schuldverschreibungen und andere
festverzinsliche Wertpapiere 6.868.033.645,73", "7. Beteiligungen
279.087.114,83"). This is exactly the structured data we want — and it's
**directly parseable from the 29% with text layers**, no OCR needed.

### 2.4 The ESEF ZIP is gold (for listed issuers)

RBI 2024 ESEF/iXBRL: **782 tagged facts** using the **standard `ifrs-full:`
taxonomy** (`ifrs-full:Assets`, `ifrs-full:CashAndCashEquivalents`,
`ifrs-full:ComprehensiveIncome`, …). Fully machine-readable with a standard
cross-issuer taxonomy — extractable with Arelle or even regex over the
`ix:nonFraction` tags. **This is the best single source for the ~12 listed AT
banks/insurers.**

### 2.5 What this means for the build

The data path splits three ways by source quality:

| Source | Coverage | Effort | Quality |
|---|---|---|---|
| **ESEF/iXBRL** (listed issuers) | ~12 entities | low (Arelle) | excellent, standard taxonomy |
| **Text-layer PDF** | ~29% of filings | medium (table extraction) | good, BWG/VAG positions present |
| **Scanned PDF** | ~71% of filings | high (OCR / vision-LLM) | variable, needs QA |
| **EBA P3DH / SFCR QRT** (external) | all banks / insurers | medium (per-source ingest) | excellent, harmonised, but prudential not statutory |

---

## 3. Detection & the FI flag

This is the launch-essential, cheap part. **Decision: yes, add an
`is_financial_institution` flag on every served record, set from external
registers, regardless of whether we have financial data for that entity.**

Rationale: the flag's whole job is to stop a downstream agent from computing
EBIT-margin / equity-ratio nonsense on a bank. It delivers value even with zero
financials ("this is a Versicherung, VAG-regulated, FMA license #X").

### 3.1 New identity fields

```python
class PresentedIdentity(BaseModel):
    fnr: str
    register_id: str
    name: str
    legal_form: str
    status: str
    court: str | None
    # NEW (V2):
    is_financial_institution: bool = False
    fi_kind: Literal["bank", "insurer", "pensionskasse", "investmentfirm"] | None = None
    fi_license_authority: str | None = None     # "FMA"
    fi_license_id: str | None = None            # OeNB BLZ (banks) / FMA Konz-# (insurers)
```

### 3.2 Detection sources — VERIFIED 2026-06-28 (deep-research, 24 sources)

> **Status:** shipped today = ONLY the name heuristic below (the "last resort"). The
> register-based, FN-keyed sources are the upgrade tracked in the FI-detection issue. The
> name heuristic is provably lossy (live recall test: missed BAWAG Group AG, Oberbank AG,
> VIG Holding AG — acronym/compound names) and over-captures subsidiaries → it must NOT
> remain the source of truth.

**Banks — authoritative, free, FN-keyed (deterministic):**
1. **OeNB lists carry the Firmenbuchnummer directly** — free CC-BY bulk CSV, no key:
   - `https://www.oenb.at/docroot/downloads_observ/MFI.csv` (monthly) — header
     `Nr;Institut;RIAD-Code;OeNB-IdentNr;FB-Nr;E-VGR;Institutsart;…;LEI` → **`FB-Nr` = Firmenbuchnummer**, plus `Institutsart` (type) + LEI.
   - `…/sepa-zv-vz_gesamt.csv` (daily, semicolon-delimited, 5 disclaimer lines first) — 22 cols incl. **`Firmenbuchnummer`**, `Bankleitzahl`, `LEI`, name. Verified row: „UniCredit Bank Austria AG = 150714p".
   - `…/NMFI.csv` — non-MFI BWG credit institutions.
   → **Direct FN join. No name matching.**

**Insurers — authoritative, free, but FN via LEI bridge:**
1. **EIOPA Register of Insurance Undertakings** — free public CSV/Excel export (weekly Fri),
   `register.eiopa.europa.eu`. Carries **LEI** + national identification code + names. **No FN**
   (the national code is the FMA-side id, ≠ FN reliably) → bridge **LEI→FN via GLEIF**.
2. **GLEIF** — free, keyless: REST `https://api.gleif.org/api/v1/lei-records/{LEI}` or the CC0
   Golden Copy bulk; field **`entity.registeredAs` = Firmenbuchnummer** for AT entities.
3. Rechtsform `VER` (VVaG) — strong positive signal, no lookup needed.

**Not usable for bulk:** the **FMA Unternehmensdatenbank** is authoritative for *licences* but is
**web-search-form only — no CSV/API**, and exposes no FN/LEI. The **ECB MFI list** is free but
carries only RIAD_CODE + LEI, **no FN** → use OeNB instead for AT banks.

**Name regex** (`Bank|Sparkasse|Raiffeisen|Volksbank|Hypo|Versicherung|…`) — **demoted to a soft
candidate hint only** (flag unmatched name-hits for review), NEVER the authoritative source.

Estimated populations: ~440-460 banks, ~75-95 insurers.

### 3.2a Reconcile cadence + the additive principle (do not gate the pipeline)

- **Weekly full reconcile** (a scheduled job, like the Firmenbuch sync): download the current
  OeNB + EIOPA lists, build the authoritative `FN → {bank|insurer}` set, then **set the flag on
  matches AND clear it on companies no longer in the lists**. One mechanism covers both
  directions — **licence lost → unflagged, new licence → flagged** — self-correcting, no special
  case (OeNB refreshes monthly, EIOPA weekly, so weekly is ample).
- **The flag is ADDITIVE CONTEXT — it must NEVER suppress XML ingestion or parsing.** A flagged
  FI that *does* file a usable UGB-XML Jahresabschluss (e.g. a small institution, a holding, or a
  given year) is parsed and served with full numbers exactly like any company; the flag only
  explains *absent* UGB figures (BWG/VAG) and points to the official PDF. So the pipeline keeps
  pulling + parsing XML for everyone (`include_pdf=True` for FIs gets XML **and** PDF, never
  PDF-only). The flag drives presentation/caveat + which firms also need the PDF, not a switch
  that turns XML off.

### 3.3 New container `00_directories`

Nightly pull from OeNB CSV + FMA register + ECB IC list + GLEIF. Schema per row:

```jsonc
{ "id": "<fnr>", "fnr": "...", "kind": "bank"|"insurer",
  "blz": "...", "fma_license_id": "...", "lei": "...",
  "name": "...", "legal_form": "...", "sparte": "leben"|"sach"|"kranken"|"re"|null,
  "license_type": "Kreditinstitut"|"...", "license_date": "...",
  "source": "oenb"|"fma"|"ecb", "last_seen_at": "..." }
```

`50_consolidate` joins on FN, sets the identity flags. **This phase alone — no
new parser, no financials — is the most important deliverable.** It removes the
worst correctness risk and enables `fi_kind` search filtering on day one.

---

## 4. Banks — BWG schema

Legal basis: **BWG §§ 43-58 + Anlage 1 (Bilanz) + Anlage 2 (GuV)**. See
[`docs/research/banks_BWG_schema.md`](../research/banks_BWG_schema.md) for the
full position lists and citations. The schema is fundamentally different from
UGB — no EBIT, no Umsatzerlöse; instead Nettozinsertrag → Provisionsergebnis →
Handelsergebnis → Betriebsergebnis → Wertberichtigungen.

### 4.1 Data sources, in build-priority order

1. **EBA Pillar 3 Data Hub** (P3DH) — XBRL, per-bank CET1/T1/Total-capital
   ratio, RWA, NPL, LCR, NSFR, leverage. The right source for *prudential*
   metrics (what investors look at). Operational 2025 for large/other
   institutions.
2. **ESEF/iXBRL** — for listed banks (RBI, etc.), full IFRS Bilanz/GuV at
   standard `ifrs-full:` taxonomy (§2.4). ~12 entities, low effort.
3. **Text-layer Firmenbuch PDF** — BWG Anlage 1/2 positions, directly parseable
   for the 29% with text (§2.3).
4. **Scanned Firmenbuch PDF** — the 71%, needs OCR / Azure Document
   Intelligence / vision-LLM.

### 4.2 Bank position taxonomy

A new `core/mapping/bwg_positions.json` parallel to the 317-entry UGB tree.
Anchor list (from the live sample, BWG Anlage 1 + 2):

- **Bilanz (Anlage 1)**: Forderungen an Kreditinstitute, Forderungen an Kunden,
  Schuldverschreibungen und andere festverzinsliche Wertpapiere, Aktien und
  andere nicht festverzinsliche Wertpapiere, Beteiligungen, Anteile an
  verbundenen Unternehmen, Verbindlichkeiten gegenüber Kreditinstituten,
  Verbindlichkeiten gegenüber Kunden, verbriefte Verbindlichkeiten,
  Eigenmittel/Eigenkapital, Bilanzsumme.
- **GuV (Anlage 2, the numbered structure)**: 1 Zinsen und ähnliche Erträge,
  2 Zinsen und ähnliche Aufwendungen (→ Nettozinsertrag), 3 Erträge aus
  Wertpapieren/Beteiligungen, 4 Provisionserträge, 5 Provisionsaufwendungen
  (→ Provisionsergebnis), Handelsergebnis, Betriebsaufwendungen,
  Betriebsergebnis, 9 Wertberichtigungen, Jahresüberschuss.

### 4.3 Bank ratios

| Ratio | From | Notes |
|---|---|---|
| Net Interest Margin | BWG Anlage 2 | Nettozinsertrag / Ø Bilanzsumme |
| Cost-Income Ratio | BWG Anlage 2 | (Personal + Sachaufwand) / Betriebsertrag |
| Loan-to-Deposit | BWG Anlage 1 | Forderungen Kunden / Verbindlichkeiten Kunden |
| ROE | BWG Anlage 1+2 | Jahresergebnis / Ø Eigenkapital |
| **CET1 / RWA / NPL / LCR / NSFR / Leverage** | **EBA P3DH** | not in any JA — prudential reporting only |

---

## 5. Insurers — VAG schema

Legal basis: **VAG 2016 §§ 136-167**, Bilanz layout **§ 144**, GuV **§ 146**,
detailed by the FMA's **VU-RLV** (BGBl. II 316/2015). Three parallel technical
accounts (Leben I / Sach II / Kranken III) + non-technical IV. Composite
insurers barred (§ 8 Abs. 4 VAG) — each AG runs one Sparte. See
[`docs/research/insurers_VAG_schema.md`](../research/insurers_VAG_schema.md).

### 5.1 Data sources, in build-priority order

1. **SFCR + public QRTs** (Implementing Reg. (EU) 2023/895, Annex I) — the
   standardised Solvency II templates: S.02.01 Bilanz, S.05 P&L by LoB, S.12/S.17
   technical provisions, S.22/S.23 own funds, S.25 SCR, S.28 MCR. Per solo
   entity + per group, annually. The right source for solvency metrics.
2. **ESEF/iXBRL** — listed insurers (VIG, UNIQA group), IFRS taxonomy.
3. **Text-layer Firmenbuch PDF** — VAG § 144/146 positions (the 29%).
4. **Scanned Firmenbuch PDF** — the 71%, OCR/vision-LLM.

### 5.2 Insurer position taxonomy

A new `core/mapping/vag_positions.json`. Anchor list (VAG § 144 / § 146):

- **Bilanz (§ 144)**: Kapitalanlagen, Kapitalanlagen für Rechnung und Risiko
  von LV-Versicherungsnehmern, Anteil der Rückversicherer an den
  versicherungstechnischen Rückstellungen, Forderungen, versicherungstechnische
  Rückstellungen (Deckungsrückstellung, Rückstellung für noch nicht abgewickelte
  Versicherungsfälle, Schwankungsrückstellung), Eigenkapital, Bilanzsumme.
- **GuV (§ 146, three technical accounts + non-technical)**: Verrechnete
  Prämien, abgegrenzte Prämien, Erträge aus Kapitalanlagen, Aufwendungen für
  Versicherungsfälle, Veränderung der versicherungstechnischen Rückstellungen,
  Aufwendungen für den Versicherungsbetrieb, versicherungstechnisches Ergebnis,
  Jahresüberschuss.

### 5.3 Insurer ratios

| Ratio | From | Notes |
|---|---|---|
| Combined Ratio | VAG § 146 | (Schadenaufwand + Betriebsaufwand) / verrechnete Prämien |
| Loss Ratio (Schadenquote) | VAG § 146 | Schadenaufwand / Prämien |
| Expense Ratio (Kostenquote) | VAG § 146 | Betriebsaufwand / Prämien |
| ROE | VAG § 144+146 | Jahresergebnis / Ø Eigenkapital |
| Kapitalanlagenrendite | VAG § 144+146 | Kapitalanlageergebnis / Ø Kapitalanlagen |
| **SCR / MCR coverage, Own-funds tiering** | **SFCR S.25/S.28/S.23** | not in the statutory JA |

---

## 6. Pipeline changes

All changes are **purely additive**. The existing 341k UGB entities flow through
unchanged; the branch happens in `50_consolidate` after the directory join.

```
                                              ┌── UGB path (existing) → financials, ratios
filings → parse → consolidate → dir-join ────┤
                                              └── FI path (new)  → fi_financials, fi_ratios
```

| Layer | Change |
|---|---|
| `00_directories` (new) | OeNB + FMA + ECB + GLEIF nightly pull. |
| `90_ingest` | Recognise `.zip` (ESEF) as a first-class artifact; keep `.pdf`. Fix large-file download (§9). |
| `70_parse` | New variants: `esef_xbrl` (Arelle), `bwg_pdf_text` / `vag_pdf_text` (text-layer table extraction), `bwg_pdf_ocr` / `vag_pdf_ocr` (Azure Document Intelligence for the 71% scanned). `variant.py` detects text-vs-scanned by chars/page. |
| `core/mapping` | `bwg_positions.json` + `vag_positions.json`, parallel to UGB. |
| `50_consolidate` | Directory-join sets `is_financial_institution`/`fi_kind`. FI filings go to a separate `fi_financials` block, never mixed with UGB `financials`. |
| `30_derive` | `bwg_ratios.py` + `vag_ratios.py` → `fi_ratios` block. UGB ratios nulled for FI entities. |
| `10_present` | Pass through both blocks + identity flags + caveat string. |

---

## 7. Layer 10 + MCP server

### 7.1 What arrives in layer 10 (served record)

```jsonc
"identity": {
  ...,
  "is_financial_institution": true,
  "fi_kind": "bank",
  "fi_license_authority": "FMA",
  "fi_license_id": "..."          // BLZ for banks, FMA Konz-# for insurers
},
"financials": {
  "caveat": "Bank (BWG) / insurer (VAG) — different statement schema than UGB. UGB ratios (EBIT, equity ratio, current ratio) are intentionally null. See fi_financials / fi_ratios.",
  // all existing UGB fields null for FI entities
},
"fi_financials": {                 // NEW, only for FI entities
  "schema": "BWG_Anlage_1_2" | "VAG_S144_S146" | "ifrs_esef",
  "source": "esef" | "pdf_text" | "pdf_ocr" | "eba_p3dh" | "sfcr_qrt",
  "latest_year": 2024,
  "positions": { /* schema-specific name → value time series */ }
},
"fi_ratios": {                     // NEW
  // bank:    { "nim": .018, "cost_income": .55, "cet1_ratio": .142, "npl_ratio": .021, ... }
  // insurer: { "combined_ratio": .94, "loss_ratio": .65, "expense_ratio": .29, "sii_ratio": 1.85, ... }
}
```

### 7.2 MCP server changes

| Tool | Change |
|---|---|
| `search_companies` | New filters `is_financial_institution`, `fi_kind`, `bank_cet1_min`, `insurer_combined_ratio_max`, `insurer_sii_ratio_min`. Result card carries `is_financial_institution` + `fi_kind`. |
| `get_company_details` | Emit `fi_financials` + `fi_ratios` when present; always emit `financials.caveat` for FI entities. |
| `get_full_record` | + the raw BWG/VAG position taxonomy. |
| `describe_fields` | Document the new fields, schemas, caveat, and which ratios come from the JA vs P3DH/SFCR. |
| `list_sectors` | Add a `financial_institutions` group with sub-types. |
| `get_coverage` | New axis: "of N detected banks/insurers, M have structured financials, K flag-only". |

Backwards compatible: existing signatures unchanged, new fields are additive,
a 341k UGB query returns identical results before/after.

---

## 8. Per-user usage metering

> (Folded in here as a chapter rather than a standalone doc.)

**Recommendation: three counters per call, daily-rollup, one middleware wrapper.
~2 days of work — easily doable.**

### 8.1 Why three counters

Calls aren't fungible — a static `describe_fields` and an expensive
`get_cohort_summary` differ ~50× in real cost. So track:

| Counter | What | Use |
|---|---|---|
| **`calls`** | flat invocation count | simple rate-limit / display |
| **`compute_units`** | weighted call cost (table below) | fair-use accounting / billing unit |
| **`ru_consumed`** | summed Cosmos `x-ms-request-charge` | exact cost vs. Azure bill (internal only) |

**Headline metric to show users = `compute_units`** (predictable, fair). RUs are
too noisy to display; raw call-count is unfair.

### 8.2 compute_units weights

| Tool | units | | Tool | units |
|---|---:|---|---|---:|
| `describe_fields` | 0 | | `get_company_details` | 2 |
| `list_sectors` | 0 | | `get_company_history` | 3 |
| `get_coverage` | 1 | | `find_peers` | 5 |
| `get_document` | 1 | | `get_cohort_summary` | 5 |
| `search_companies` (≤25) | 1 | | `get_full_record` | 5 |

Rule of thumb: 1 unit ≈ 5 Cosmos RU. **Free-tier soft cap: 1,000 units/key/day**
(~500 detail calls — generous for real agent use, safe against runaway scripts).

### 8.3 Data model — Cosmos container `00_usage`

One doc per `(key_hash, day_utc)`, partition key = `key_hash`:

```jsonc
{
  "id": "u_<sha256(key)[:16]>_2026-07-01",
  "partition_key": "<key_hash>", "key_hash": "<key_hash>",
  "day_utc": "2026-07-01", "tier": "free",
  "calls": 1842, "compute_units": 3127, "ru_consumed": 15843.2, "bytes_out": 8204321,
  "by_tool": { "search_companies": {"calls":612,"compute_units":612,"ru_consumed":4233.1}, ... },
  "errors": { "rate_limited": 12, "auth_failed": 0, "tool_error": 3 },
  "first_call_at": "...", "last_call_at": "...",
  "_meta": { /* lineage */ }
}
```

Daily granularity (not per-call): bounded write volume, natural read pattern,
GDPR-light. Container indexes only `day_utc` + `key_hash`; TTL 365 days.

### 8.4 Implementation

A `metered(tool_name)` decorator in `packages/auth/src/fbl_auth/metering.py`
wraps every MCP tool (one-line change at registration, no per-tool edits). A
`contextvars` accumulator sums `x-ms-request-charge` from every Cosmos read in
`fbl_core.storage.cosmos`. On call end, an atomic Cosmos `patch_item` with
`incr` ops updates the daily doc (atomic → parallel calls by the same key are
race-safe).

### 8.5 Exposure

- **`get_my_usage(window)`** — new MCP tool, returns the caller's own usage
  (calls + compute_units + remaining quota + by_tool); excludes `ru_consumed`.
- **Admin tools** (`admin_list_users`, `admin_get_user`, `admin_set_quota`) on a
  separate IP-restricted endpoint, owner-key gated. Not on the public MCP server.

### 8.6 Privacy

Only the SHA-256 key hash — no email/IP/UA. The `key_hash → email` mapping
exists only in `00_accounts` (owner-scope to join). The usage doc is
GDPR-anonymous on its own. 365-day TTL.

### 8.7 Build order

| Phase | Scope | Effort |
|---|---|---|
| 1 | `00_usage` container + `metered()` decorator + `calls`/`compute_units` | 1 d |
| 2 | Cosmos RU accounting via header | 0.5 d |
| 3 | `get_my_usage` tool + quota enforcement | 0.5 d |
| 4 | Admin tools (separate endpoint) | 1 d |

Phase 1-3 = ~2 days to full per-user visibility.

---

## 9. Ingest-gap fix

Two sub-fixes, both generic (help all companies, not just FI).

### 9.1 Finish the filing-check backfill (Problem A)

437,292 companies have never had `sucheUrkunde` run. The orchestration's
filing-check phase needs to drain that queue. This is throughput, not a bug —
verify the scheduled job is processing the never-checked queue and let it run.
Prioritise high-value entities (e.g. all `is_financial_institution` once §3
lands, large companies, recently-active) ahead of the long tail.

### 9.2 Fix the large-file download (Problem A2)

`urkunde` fails on multi-MB filings with `"failed after N attempts: http 200"`
— 38% of our large-file sample pulls. Root cause is in
[`soap_client.py:_post`](../../products/agentic-firmenbuch/packages/firmenbuch_client/src/fbl_firmenbuch_client/soap_client.py):
a 200 response whose body can't be parsed as XML within the timeout falls into
the retry path and eventually dead-letters. Fixes to implement & test:

1. **Raise the per-request timeout** for `urkunde` specifically (the document
   call) — large base64 PDFs need more than the default 60 s. Sample pulls
   succeeded at 180 s.
2. **Stream the response** instead of buffering the whole SOAP envelope in
   memory before parsing.
3. **Distinguish "200 but truncated/unparseable" from "200 OK"** — currently
   both look like `http 200`; add explicit detection (content-length mismatch,
   incomplete base64) and a longer backoff for genuinely large payloads.
4. **Re-drive the 5,830 dead-lettered entities** once the download is hardened.

Even with PDF-only FI filings, fixing this lets us serve **filing dates + the
PDF doc_key link** ("Erste Group Bank filed for FY2024 → [open PDF]") — real
value before any structured extraction lands.

---

## 10. Build order & effort

| Phase | Chapter | Scope | Effort | Deliverable |
|---|---|---|---|---|
| **0** | §9.1 | Drain the filing-check backfill | ops only | More of the 437k get *any* data |
| **1** | §9.2 | Harden large-file `urkunde` download | 2-3 d | Bank/insurer PDFs actually download; filing dates + doc links served |
| **2** | §3 | Detection + `is_financial_institution` flag + caveat | 3 d | Agents stop UGB-reasoning on banks; FI search filters work; **launch-essential** |
| **3** | §4, §2.4 | ESEF/iXBRL parser (Arelle) | 1 w | Listed banks/insurers (~12) get full IFRS Bilanz/GuV |
| **4** | §4 | EBA P3DH ingest (banks) | 1 w | CET1/NPL/LCR/leverage for every AT bank |
| **5** | §5 | SFCR/QRT ingest (insurers) | 1-2 w | Combined ratio / SII coverage for every AT insurer |
| **6** | §4, §5 | Text-layer PDF extraction (BWG/VAG) | 1 w | The 29% text PDFs → statutory Bilanz/GuV time series |
| **7** | §4, §5 | Scanned-PDF OCR (Azure Document Intelligence / vision-LLM) | 2-3 w | The 71% scanned PDFs → same, with QA |
| **8** | §8 | Per-user usage metering | 2 d | Full consumption visibility per key |

**The critical, cheap wins are Phases 1+2** (~1 week): the data downloads, and
the flag removes the worst correctness risk. Phases 3-5 add real structured
financials from the best sources. Phases 6-7 close the long tail from the
Firmenbuch PDFs themselves. Phase 8 (metering) is independent and can land any
time.

---

## Reference research (citations)

- [`docs/research/jab40_bank_insurer_support.md`](../research/jab40_bank_insurer_support.md) — JAb 4.0 is UGB-only by design (XSD + BMJ docs).
- [`docs/research/banks_BWG_schema.md`](../research/banks_BWG_schema.md) — BWG §§ 43-58, Anlagen 1/2, EBA P3DH, OeNB Bankstellenverzeichnis.
- [`docs/research/insurers_VAG_schema.md`](../research/insurers_VAG_schema.md) — VAG 2016 §§ 136-167, § 144/146, SFCR/QRT, ECB IC list + FMA register.
