# Rechtsform Coverage — how the pipeline handles every Austrian legal form (v1)

**Purpose.** Answer, per Austrian legal form (Firmenbuch `RECHTSFORM` code): does the pipeline
produce real consolidated/presented financial data for it, and — where it doesn't — *why*, and
what it would take to change that. **Everything here was verified against the live data** (June
2026): real sample filings of each form were run through the actual `parse → consolidate →
derive → present` code, and the JAb 4.0 XSD was inspected. No assumptions.

## TL;DR

- **The pipeline is genuinely legal-form-agnostic.** There is no `if rechtsform == …` branch
  anywhere in parse/consolidate/derive/present. Whether a company yields financials depends only
  on whether it has a **modern, position-bearing UGB Jahresabschluss XML** — *not* on its form.
- **Already works (proven with real 2024/2025 samples): GmbH (GES), AG, KG, OG, Genossenschaft
  (GEN), SE, Einzelunternehmer (EU), Privatstiftung (PST).** To process any of them, just add the
  code to `PROCESS_RECHTSFORMEN` (the `backfill-process` worklist) — **no code change needed**.
- **Out of scope by construction: banks (SPA) and insurers (VER).** They do not deliver a UGB
  Jahresabschluss XML at all — the JAb schema has no bank/insurance document type, banks filed
  **0** statements in our universe, and no insurer has a modern position-bearing XML. They report
  under separate regimes (BWG → OeNB/FMA; VAG → FMA). Covering them is a *separate ingestion
  project*, not a parser tweak.
- **Across every form**, PDF-only and pre-~2013 "skeleton" XML filings carry no machine-readable
  positions, so they produce an empty `financials` block (the document is still linked). Extracting
  those would require PDF parsing — a separate capability, out of v1 scope.

## The universe (live, June 2026) — active companies vs. who actually files

Counted over all 343,556 active companies in `99_registry`; "filed" = has ≥1 recorded
Jahresabschluss (`known_filings` non-empty).

| Form | code | active | filed | filed % | parses to financials? |
|------|------|-------:|------:|--------:|-----------------------|
| GmbH | `GES` | 213,798 | 191,669 | 89.6 % | **Yes** (baseline) |
| Einzelunternehmer | `EU` | 56,468 | 20 | 0.04 % | Yes, when filed (rare) |
| Kommanditgesellschaft | `KG` | 42,280 | 12,069 | 28.5 % | **Yes** |
| Offene Gesellschaft | `OG` | 22,473 | 443 | 2.0 % | **Yes** |
| Privatstiftung | `PST` | 2,956 | 1 | 0.03 % | Yes, when filed (rare) |
| (no rechtsform) | `(none)` | 2,358 | 16 | 0.7 % | n/a — change-feed stubs |
| Genossenschaft | `GEN` | 1,986 | 270 | 13.6 % | **Yes** |
| Aktiengesellschaft | `AG` | 1,163 | 481 | 41.4 % | **Yes** |
| Societas Europaea | `SE` | 43 | 7 | 16.3 % | **Yes** |
| Sparkasse / Kreditinstitut | `SPA` | 21 | **0** | 0 % | **No data at all** |
| Versicherungsverein | `VER` | 10 | 4 | 40 % | **No usable data** |

(`KEG`/`OHG` from the enumeration code list returned no active companies. The **HaRÄG 2005**
abolished OHG/OEG/KEG effective 1 Jan 2007; **UGB §907 converts them *ex lege*** to OG resp. KG (an
OHG may keep its "OHG" name suffix indefinitely, OEG/KEG had to change by 2010). So legacy "OHG"
strings can legitimately appear with legal form = OG. Defensive mapping: treat **OEG→OG, KEG→KG**;
don't assume zero occurrences in raw/historical data.)

## What "filed" means here (and what other documents exist)

"Filed" in the table above = the company has at least one **Dokumentart `48` (Jahresabschluss)**
document in the Firmenbuch Urkundensammlung (that is the only type the ingest records). A company
counted as **not filed has _no annual financial statement_** — but it may still have *other*
document types we deliberately don't ingest, because they aren't financial data. The Urkundensammlung
catalog (official `DOKUMENTART` codes): **`48` Jahresabschluss**, `49` Konzernabschluss, `01` Satzung,
`02` Gesellschaftsvertrag, and others (Anmeldungen, Beschlüsse, …). `sucheUrkunde` returns *all* of
them with their code; the pipeline keeps only `48`.

For a **financial-data product** (the MCP's purpose), only `48` (and arguably `49` Konzernabschluss,
not yet parsed) carry the figures. Incorporation/contract documents (`01`/`02`) are legal paperwork,
not financials — out of scope for v1 (they could be *linked* by reference, never parsed into
metrics). So "EU/PST/ordinary-KG don't file" means "no public annual statement to turn into
financial data," not "no Firmenbuch presence" — their **master data** (name, seat, status, organs)
is always available and already in `99_registry`.

> NOTE: an exact per-company document-type inventory (which non-filers have which `01`/`02`/… docs)
> was not probed live — the HVD API key is bound to the Azure deployment and not readable locally; a
> one-off probe job can produce it if ever needed. The catalog above is from the official
> JustizOnline API reference.

## Coverage among filers (larger sample, N=50 per form)

The 1–3-sample run proved *capability*; this larger run measures **what share of a form's
*filers* actually yields financials** (ran up to 50 filed companies per form through the full
pipeline; "yield" = `has_bilanz` after consolidate). This isolates extraction quality from the
who-files question.

| Form | sampled (filers) | yield financials | coverage | with GuV |
|------|-----------------:|-----------------:|---------:|---------:|
| GES (GmbH) | 50 | 50 | **100 %** | 1 |
| KG | 50 | 50 | **100 %** | 3 |
| SE | 7 | 7 | **100 %** | 7 |
| EU | 20 | 20 | **100 %** | 0 |
| PST | 1 | 1 | **100 %** | 0 |
| OG | 50 | 49 | **98 %** | 4 |
| AG | 50 | 30 | **60 %** | 30 |
| GEN | 50 | 16 | **32 %** | 11 |
| VER | 4 | 0 | **0 %** | 0 |
| SPA | 0 filers | — | — | — |

Reading it: **GES/KG/OG/SE/EU/PST ≈ full coverage** among filers — when they file, we extract the
financials. **AG ~60 % and GEN ~32 %** are lower because those populations carry a larger share of
**old PDF-only / pre-2013 skeleton filings** (no machine-readable positions) — and for GEN, some
filers are **banking cooperatives** on the BWG schema. This is the *same vintage/medium effect*
described below, just more pronounced for those two forms — **not a parser defect** (a modern AG/GEN
XML parses to 137/119 tags of full financials, shown above). Low `with GuV` counts reflect size:
small companies file an abbreviated Bilanz and omit the GuV (§278) — expected, not a gap. (Sample
is unordered `TOP 50`, so treat as ±a few points, not exact.)

## Document-type (`DOKUMENTART`) codes — the full picture

The user asked: what are all the document types, are all the numeric codes documented, and which
belong in a later phase? Findings (verified by independent research + our reference + an empirical
archive scan; **no complete public catalog exists**):

**Confirmed numeric `DOKUMENTART.CODE` (the only ones publicly documented anywhere):**

| Code | Description | Financial? | Ingest? |
|------|-------------|-----------|---------|
| `48` | Jahresabschluss (annual statement) | **Yes** | ✅ v1 (the only one) |
| `49` | Konzernabschluss (consolidated group statement) | **Yes** | ⏳ **phase 2** |
| `01` | Satzung (articles) | No (legal) | ✘ (link-only at most) |
| `02` | Gesellschaftsvertrag (partnership agreement) | No (legal) | ✘ (link-only at most) |

**There is no published complete `DOKUMENTART` code table.** The full enumeration lives only in the
**gated** JustizOnline HVD WebService docs ZIP and the SOAP WSDL/XSD (both require the `X-API-KEY`).
Public/secondary sources (data.gv.at, ERV help, community repos) describe document *categories* in
prose but publish only the four codes above. Categories that exist in the Urkundensammlung but whose
**numeric codes are not public**: Anmeldung, Beschluss (General-/Hauptversammlung), Protokoll,
Prüfungsbericht, Eröffnungsbilanz, Musterzeichnung, Gesellschafterliste, Verschmelzungs-/Spaltungs-/
Umwandlungsvertrag. (`48`/`49` also umbrella attached financial reports — Bestätigungsvermerk,
Lagebericht, CSRD/sustainability, Zahlungsbericht.)

A *different, fully-published* taxonomy exists — the ERV **filing-side** `U####`/`T####`/`P###` codes
(`U1000` Satzung, `U1700` Gesellschaftsvertrag, `U5000` Anmeldung, `U1400` Verschmelzungsvertrag, …,
in the Justiz `FB_EinschreiterINFO.pdf`). It is **not** the `sucheUrkunde` `DOKUMENTART` taxonomy and
there is no published crosswalk — use it only as a concept map.

**Empirical catalog — what our HVD tier ACTUALLY returns (live probe, 231 companies, 2026-06-22).**
The `diag-doctypes` orchestrator mode harvested the distinct `DOKUMENTART` code:text pairs live. The
tier exposes **19 accounting-related document types** (no `01`/`02` legal docs appeared — the HVD is
the "company accounts" dataset):

| Code | Text | Note |
|------|------|------|
| `48` | Jahresabschluss | the primary statement — **what v1 ingests** |
| `137` | Jahresabschluss berichtigt | **corrected** JA — supersedes the original ⚠️ (see below) |
| `164` | Jahresabschluss vorläufig/unvollständig | preliminary/incomplete |
| `83` | Schlussbilanz | closing/liquidation balance |
| `49` | Konzernabschluss | consolidated group accounts → **phase 2** |
| `27` | Bestätigungsvermerk zum Jahresabschluss | audit opinion (attachment) |
| `51` | Lagebericht | management report (attachment) |
| `151` | Anhang zum Jahresabschluss | notes (attachment) |
| `73` `70` | Protokoll d. Haupt-/Generalv. m. Jahresabschluss | AGM minutes bundling the JA |
| `128` `129` `124` | Konzernabschluss begleitende (Protokoll/Bestätigungsvermerk/Lagebericht) | group-accounts attachments |
| `132` `133` | Corporate Governance Bericht (+ konsolidiert) | governance report |
| `139` `140` | Nichtfinanzieller Bericht (+ konsolidiert) | CSR/non-financial |
| `173` | Zusicherungsvermerk konsolidierter Nachhaltigkeitsbericht | sustainability assurance |

⚠️ **Important finding for v1:** the ingest filter `is_jahresabschluss` keeps **only code `48`** — so it
currently **misses `137` (Jahresabschluss berichtigt = a corrected statement that supersedes the
original)**, and also `83` Schlussbilanz / `164` preliminary. `137` is the one with data-correctness
impact: if a company filed a `48` then a `137` correction, we serve the *uncorrected* figures.
Counts are small (6 `137` and 1 `164` in 231 companies ≈ <3%), so it's a **low-frequency edge case**,
not a launch blocker — but a recommended **v1.x fix**: treat `48`/`137`/`164`/`83` all as Jahresabschluss
variants, newest-per-Stichtag wins (a `137` overrides the `48` for that fiscal year). Re-run
`diag-doctypes` anytime to refresh this catalog.

**Phase-2 decision (my call, as agreed):** add **`49` Konzernabschluss** (the one other *financial*
document type — value for large groups; different consolidated/IFRS structure, so a real parser
extension). `01`/`02` and the rest are legal/administrative — at most *linked* by reference in the
MCP, never parsed into metrics. Banks/insurers remain a separate regime (see above), independent of
this code question.

## How the parser actually works (the mechanism that makes it form-agnostic)

A Jahresabschluss is filed under the **same UGB §224 (Bilanz) / §231 (GuV) Gliederung regardless
of legal form**. The structured XML (legacy FinanzOnline `UEBERMITTLUNG`/`BILANZ_GLIEDERUNG`, and
JAb 4.0) carries the figures as `POSTENZEILE`/`BETRAG` rows keyed by `HGB_224*` codes. The parser
extracts **by code**, never by form. So a GmbH, AG, KG, OG, Genossenschaft, SE, sole trader or
private foundation all flow through identically.

**JAb 4.0 schema check (XSD):** the document-type enum (`JAHRESABSCHLUSS_KONZERNABSCHLUSS`) is
exactly `JAB`, `JAB-ANLAGE12`, `JAB-ANLAGE32`, `KAB` — i.e. standard full / small / micro UGB
statements plus the consolidated (Konzern) statement. **There is no bank or insurance document
type in the schema** — strong evidence those sectors are simply not represented here.

What determines whether a given company yields financials is therefore purely **data vintage /
medium**, identical across forms:

- **Modern position-bearing XML** (≈2013+, full `POSTENZEILE`/`BETRAG`) → full financials. ✅
- **Pre-~2013 "skeleton" XML** (`BILANZ_GLIEDERUNG` present but **no** position rows) → empty. ⚠️
- **PDF-only** filing (older years, or filers who only submitted a PDF) → not parsed; the document
  is still linked, `financials` is empty. ⚠️ (true for GmbHs too.)

## Per-form findings (with the real samples that were run)

For each form below, a real active filer was run end-to-end. "Positions/tags" = distinct XML tags
in the newest filing (a skeleton XML has ~37–47 tags; a populated one ~85–140).

- **GmbH (`GES`)** — baseline. e.g. `139367b`: full Bilanz, Bilanzsumme €0.85 M, EK-Quote 79 %,
  18-year history. The 213k-strong bulk run is GES-first.
- **AG (`AG`)** — **parses fully** with modern XML: `064499b` newest filing 2025, positions=True,
  137 tags. (An older AG like `046138a` is PDF-only 2006–2020 → empty — a vintage artefact, not a
  form problem.)
- **KG (`KG`)** — **parses fully**: `004265y` Bilanzsumme €5.95 M, EK-Quote 72 %, history 2014–2025.
  The 12,069 filers are overwhelmingly *kapitalistische* KGs (GmbH & Co KG), which are UGB
  rechnungslegungspflichtig — hence the high filing count for a "partnership".
- **OG (`OG`)** — **parses fully**, incl. GuV: `027015d` Bilanzsumme €903 M (a large kapitalistische
  OG). Most ordinary OGs (natural-person partners) are not rechnungslegungspflichtig → 2 % file.
- **Genossenschaft (`GEN`)** — **parses fully** with modern XML: `093299f` 2025, positions=True,
  119 tags. (Older `038648k` is PDF-only → empty.) Genossenschaften follow UGB accounting (GenG §22
  → UGB Book 3) with cooperative terminology; Offenlegung is size-gated (§221 thresholds). **Caveat:
  banking cooperatives** (Raiffeisen / Volksbank = Kreditgenossenschaften) are credit institutions
  and therefore use the **BWG bank schema**, not UGB §224/§231 — a handful of GEN entries may
  therefore behave like banks (empty under the UGB parser). Tiny count; not a v1 concern.
- **SE (`SE`)** — **parses fully**, incl. GuV: `421240x` Bilanzsumme €613 M, EK-Quote 90 %. Treated
  exactly like an AG.
- **Einzelunternehmer (`EU`)** — **parses fully when present**: `509777y` Bilanzsumme €0.42 M, 5-year
  history. Only ~20 of 56k file — sole traders are only rechnungslegungspflichtig above the UGB §189
  turnover thresholds, and most below them never file. Nothing to fix; there is simply little data.
- **Privatstiftung (`PST`)** — **parses fully when present**: `242314w` Bilanzsumme €8.9 M, EK-Quote
  63 %. But only **1 of 2,956** has a public statement — private foundations generally report to
  their Stiftungsprüfer, not the public Firmenbuch. Little data, by regime.
- **Sparkasse / bank (`SPA`)** — **0 of 21 filed** in our parsed corpus. Banks *do* have a public
  Offenlegungspflicht (they file with the Firmenbuch), **but under the special BWG bank schema, not
  UGB §224/§231** — **BWG §43(1) explicitly *excludes* UGB §§224 and 231** and §43(2) mandates the
  BWG-Anlage Formblätter (Formblatt A Bilanz / B GuV): liquidity-ordered assets, no
  Anlage-/Umlaufvermögen split, bank-specific items (Haftrücklage, Fonds für allgemeine Bankrisiken).
  So their statements are simply **not in the UGB JAb XML channel** this pipeline parses → absent
  here. Out of scope (separate BWG schema + source).
- **Versicherungsverein (`VER`)** — 4 of 10 "filed", but on inspection **none has a modern
  position-bearing XML**: three (`097361d`, `078330t`, `106532s`) have only a single 2007 skeleton
  XML (no positions), one (`251897m`) is PDF-only. Insurers also have a public Offenlegungspflicht
  (VAG 2016 §246) **but under the special VAG insurance schema** — **§144 Bilanz** (assets led by
  *Kapitalanlagen*; liabilities dominated by *versicherungstechnische Rückstellungen*) and **§146 GuV
  in Staffelform** split into a *versicherungstechnische* vs *nicht-versicherungstechnische Rechnung*
  (no §231 analogue). Not the UGB JAb XML → no usable data here. Out of scope.

## What it takes to "make the pipeline work" for each form

1. **Standard UGB forms — `AG`, `KG`, `OG`, `GEN`, `SE`, `EU`, `PST`: nothing to build.** The code
   already produces correct consolidated + presented documents (proven above). To populate them into
   `10_presentation`, set the `backfill-process` worklist env, e.g.
   `PROCESS_RECHTSFORMEN=GES,AG,KG,OG,GEN,SE,EU,PST`, and let the (parallel, bounded, resumable)
   `backfill-process` job run. The percentile cohort is computed within each run over whatever is
   consolidated, so a per-form or all-forms run are both valid. **Recommended rollout: GES first
   (running now), then add `KG` (12k filers — the next-biggest real dataset), `AG`, `OG`, `GEN`,
   then the long tail (`SE`, `EU`, `PST`).**

2. **PDF-only / skeleton filings (all forms, incl. GmbH): would need PDF extraction.** A meaningful
   share of older filings exist only as PDF. Parsing them needs an OCR/PDF-table-extraction stage
   (the document is already archived + linked). **Separate capability, post-v1.** Not form-specific.

3. **Banks (`SPA`) and insurers (`VER`): a separate ingestion + schema project.** They do not deliver
   a UGB JAb XML; their statements live in the BWG (bank) / VAG (insurance) regimes (OeNB/FMA), with
   their own balance-sheet/P&L layouts (Formblätter) that differ from UGB §224/§231. Supporting them
   would mean: (a) a new source for those statements, (b) new position taxonomies in
   `core/mapping/`, (c) form-aware parsing. Given the tiny counts (21 banks, 10 insurers) this is
   **explicitly out of v1 scope** and low value. If ever wanted, it is a standalone workstream.

## What to expose in the MCP (decision)

**Include every active company of every Rechtsform** — not just the financial filers. Master data
(name, seat, status, legal form, organs/Geschäftszweig where present) is **archived for all forms,
verified incl. non-filers** (e.g. Sparkassen, Privatstiftungen, e.U., Versicherungen all return a
master record). So a company with no Jahresabschluss still becomes a useful **master-data-only**
entry (search / KYC / research), with an empty `financials` block. This is the right default: the
register is more valuable as a *complete* directory than a GmbH-only financial set, and it costs
nothing extra — master is already ingested. Implementation = run `backfill-process` for **all**
forms (widen `PROCESS_RECHTSFORMEN`), **after** the GES pass completes (so GmbH data goes live first;
the two-phase gate means a wider worklist would otherwise delay GES presentation until everything is
consolidated).

**Document types to ingest:** v1 keeps **Dokumentart `48` (Jahresabschluss)** only — it carries the
figures and covers the vast majority. Candidates for later, with my take:
- **`49` Konzernabschluss** (consolidated group accounts) — *has* financial value for big groups
  (AGs, large GmbHs), but a different (consolidated, sometimes IFRS) structure → a **post-v1**
  parser extension, not a v1 must.
- **`01` Satzung / `02` Gesellschaftsvertrag** — legal founding documents, **no financial data**;
  at most *linked* by reference in the MCP (never parsed into metrics). Low priority.
- Other types (Anmeldungen, Beschlüsse) — administrative, no product value.

## Bottom line

The original worry — "we'd have to adapt the pipeline for each Rechtsform" — does **not** hold for
the standard UGB universe: GmbH, AG, KG, OG, Genossenschaft, SE, Einzelunternehmer and
Privatstiftung are **already fully handled by the same code**, and the only lever to surface them is
the `PROCESS_RECHTSFORMEN` worklist. The genuine gaps are (a) PDF-only historical filings (a generic
PDF-parsing capability, not per-form) and (b) banks/insurers (a separate regulatory regime entirely,
out of v1 scope). See Technische Spezifikation §15b items 20a/20b for the cross-reference.

## Regulatory basis (sourced — corroborated by independent legal research)

The empirical data above lines up exactly with the statute. The whole question reduces to one
chain in the **UGB** (RIS consolidated text, Gesetzesnummer 10001702):

- **§189** — *Rechnungslegungspflicht*: Kapitalgesellschaften & *kapitalistische* Personengesellschaften
  unconditionally; everyone else only above turnover **€700k over two consecutive years** (effect
  from the second following year), or **€1m in one year** (accelerated trigger). (So "€700k/€1m",
  not "raised to €1m".)
- **§222** → prepare a Jahresabschluss; **§224 (Bilanz) / §231 (GuV)** → the mandatory standard
  Gliederung this pipeline parses.
- **§277–283** — public *Offenlegung* with the Firmenbuchgericht (9 months; size reliefs §278/§279;
  Zwangsstrafen §283) — **named for Kapitalgesellschaften only**.
- **§221 Abs 5** — extends the §277 filing to **kapitalistische Personengesellschaften** (the
  §189 Abs 1 Z 2 case: *no natural person ultimately bears unlimited liability* — i.e. GmbH & Co KG).
  This is exactly why ~28% of KGs file and ~2% of OGs do: only the kapitalistische ones must.

Per-form legal basis & sources (all RIS / official portals):

| Form | Public Offenlegung? | Basis | Layout |
|------|--------------------|-------|--------|
| GmbH, AG, SE | **Yes** | §277 (Kapitalgesellschaft); SE via SE-VO → national AG law | UGB §224/§231 |
| KG / OG | only *kapitalistische* (GmbH&CoKG etc.) | §221 Abs 5 → §277 (VfGH-confirmed carve-out for natural-person partners) | UGB §224/§231 |
| Genossenschaft | Yes, size-gated | GenG §22 → UGB §277 ff | UGB §224/§231 (coop terms) |
| Privatstiftung | **No** | PSG §21 — to Stiftungsprüfer/organs only, no §277 equivalent | n/a publicly |
| Einzelunternehmer | **No** | §277 names only Kapitalgesellschaften | n/a publicly |
| Sparkasse / bank | Yes, but **BWG schema** | **BWG §43** — *excludes* UGB §224/§231, mandates Formblatt A/B | special bank |
| Versicherung (VVaG) | Yes, but **VAG schema** | VAG 2016 §246 (disclose), §§144/146 (layout) | special insurance |

Key source URLs: UGB (consolidated) `ris.bka.gv.at` Gesetzesnummer 10001702; §189 thresholds
(`…&Paragraf=189`); §907 HaRÄG conversion (`…&Paragraf=907`); BWG §43 bank Formblätter
(`…Gesetzesnummer=10004827&Paragraf=43`); VAG 2016 §§144/146
(`…Gesetzesnummer=20009095&Paragraf=144`/`146`); PSG (`…Gesetzesnummer=10003154`); USP
Bilanzveröffentlichung & Genossenschaften pages; JustizOnline §221-Abs-5 filing form (confirms
"GmbH & Co KG … kleine kapitalistische Personengesellschaften").

**Bonus (resolves a §16 open item):** the research confirmed the **official JAb 4.0 XSD is
published & downloadable** on the Justiz edict portal (`edikte.justiz.gv.at` / `kundmachungen.justiz.gv.at`),
namespace `ns://justiz.gv.at/Bilanzierung/v4.0/Bilanz`, root `UEBERMITTLUNG`. JAb 4.0 superseded
3.31/3.32 for Firmenbuch filings from 1 Jan 2025. We already vendored these XSDs in
`docs/reference/jab40_struktur/` — so the parser can validate against the real schema, no sample
filing needed.

### Residual uncertainties (explicitly not resolved — flagged, not guessed)
- The GmbH-&-Co-KG share of the ~42k KGs: **no public statistic exists**; only derivable from the
  Komplementär type in the data itself.
- Whether residual `OEG`/`KEG` suffix strings still surface in raw data — unconfirmed; handled
  defensively (map to OG/KG).
- Banking-cooperative → BWG schema: inferred from the general credit-institution rule, not verified
  against BWG §43 for the cooperative case specifically.
