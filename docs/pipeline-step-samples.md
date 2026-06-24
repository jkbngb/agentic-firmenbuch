# Pipeline Step Samples & Data-Lineage Contract

**Purpose:** a data-format reference. One sample output document for **every pipeline stage**,
chained together for a single company (**Schubert CleanTech GmbH, FNR `093450b`** — figures from a
real published Jahresabschluss, which is public data under CC BY 4.0), so the metadata/lineage block
and per-stage shapes are documented in one place. These samples also serve as **golden fixtures**
for the test suite.

> Hashes (`sha256:…`), `doc_id` UUIDs, and a couple of fields (employee counts, exact JAb 4.0 element paths) are **illustrative placeholders**; the financial figures are real. Personal data (officer names) is shown only in its GDPR-gated form (`EXPOSE_PERSONAL_DATA=false` — age/birth-year only).

---

## Part 1 — The `_meta` lineage contract

Every document at every stage carries a `_meta` block. The rules:

- **`doc_id`** — a random **UUIDv4** generated for *this* document at *this* stage. Immutable once written.
- **`entity_id`** — the business key the document is about: `"093450b"` for a company, `"093450b/2025-12-31"` for a single filing. (Separate from `doc_id` so one can find "the current parsed doc for this filing" without knowing its uuid.)
- **`stage`** — one of `raw | parsed | consolidated | derived | presented` (coarse stages on purpose — see "overkill" below).
- **`producer`** — `module@semver`, e.g. `parse@1.0.0`. Tells you exactly which code version produced it (reproducibility).
- **`run_id`** — the pipeline run that produced it, e.g. `2026-06-16-daily-0003`. Groups everything from one daily batch for debugging.
- **`source`** + **`license`** — `justizonline_firmenbuch_hvd` + `CC-BY-4.0` (attribution travels with the data).
- **`content_hash`** — `sha256:…` over the **canonical content of the document with the volatile meta excluded** (i.e. exclude `content_hash` itself, `timestamps`, and `lineage`). **Key rule:** hash the *data*, not the timestamps — otherwise every re-run produces a new hash and change-detection/idempotency is lost. With this rule, "same inputs ⇒ same hash", so unchanged companies can be skipped cheaply.
- **`timestamps`** — ISO-8601 UTC `2026-06-16T17:30:00Z`, **accumulated** across linear stages: `{ ingested_at, parsed_at, consolidated_at, derived_at, presented_at }`. Each stage adds its own and carries the earlier ones forward.
- **`lineage`** — array of upstream provenance entries, one per prior step, each `{ stage, doc_id, content_hash, created_at, producer }`. This is the "each downstream file includes the uuid from the previous step" requirement — the full chain raw→parsed→consolidated→derived→presented is walkable by `doc_id` + verifiable by `content_hash`.

### Field renames → `source_field`
Renaming happens in exactly **one** place: the **parse** step (raw XML element → canonical field). So provenance for renames lives there, as a compact **`field_provenance.map`** (`canonical_field → source XML path`) plus the `mapping_version` and whether `WERT_TSD` scaling was applied. Downstream stages keep canonical names unchanged, so they only carry `mapping_version` — no need to repeat per field. **Derived** fields (ratios, growth) get a one-time **`derivations`** catalog (`field → formula`), not per-value annotations. (See "overkill".)

### New filing arrives → previous hashes
When a new Jahresabschluss lands, the company's `consolidated` doc is **rebuilt from all filings in immutable raw**, so no history is ever lost. The rebuild references prior hashes two ways:
- **`inputs[]`** — every parsed filing that fed this build, each with its own `doc_id` + `content_hash` (so you can see exactly which input is new/changed).
- **`supersedes`** — a pointer to the *previous* consolidated doc: `{ doc_id, content_hash, data_version, built_at }`. So you get a hash chain of versions, and `data_version` increments each rebuild.

Previous hash values are carried, both per-input and as a supersedes pointer.

---

## Part 2 — Best-practice assessment

**Keep (this is best practice):** doc-level UUID + content hash + carried-forward timestamps + upstream lineage chain; immutable raw as source of truth; `supersedes` version pointer; parse-step `field_provenance`; derive-step `derivations` catalog; `producer@version` + `run_id`. This mirrors W3C PROV / OpenLineage without the heavyweight tooling.

**Add (small gaps worth closing):**
1. **`run_id`** (added above) — group a day's outputs for ops/debugging.
2. **Hash the content, not the timestamps** (the refinement above) — needed for idempotency.
3. **Data-quality flags in `_meta.checks`** — e.g. `aktiva_equals_passiva: true`, `wert_tsd_applied: false`, `prior_year_reconciled: true`. Quality is part of provenance; store the check results so you can trust (or quarantine) a record.
4. **Public vs internal provenance split** — the internal `consolidated`/`derived` docs carry the full `_meta` (hash chain, inputs). The **`presented`** doc that the MCP serves should expose only a **trimmed `provenance`** (source, license/attribution, `data_version`, `built_at`, document links) — not the internal hash chain (it's plumbing, and exposing it is clutter + mild leakage). Shown in the presented sample.

**Overkill — avoid:**
1. **Per-field inline provenance on every value** (e.g. wrapping each number as `{value, source_field}`). With hundreds of line-items × years that bloats the doc enormously. Use the single `field_provenance.map` at parse + `derivations` catalog at derive instead — same information, ~1% of the size.
2. **A fresh UUID for every micro-step.** Keep stages **coarse** (5 stages). If you mint a uuid for every tiny transform you get lineage noise. Five well-defined stage docs per entity is the sweet spot.
3. **Storing every historical version of the consolidated doc forever.** You don't need to: raw is immutable and the build is deterministic, so any past state is rebuildable. Keep the **current** doc + the `supersedes` hash pointer (a chain of references), not full copies of every version. (If you later want true time-travel, add an append-only version store — but that's a v2 nicety, not a v1 requirement.)

Net: this design is close to best practice. The refinements are the hash-excludes-timestamps rule, the field-provenance-as-a-map (not per field), coarse stages, and a trimmed public provenance.

---

## Part 3 — Sample documents, one per stage (chained)

### Stage 0 — `raw` (manifest sidecar; the artifact itself is the XML/PDF in Blob)

The raw stage stores the **untouched** downloaded bytes (XML *and* the PDF) in Blob; this JSON is the catalog/manifest entry that gives them identity and a hash.

```jsonc
{
  "entity_id": "093450b/2025-12-31",
  "artifact": {
    "blob_path": "raw/093450b/2025-12-31/093450b_2025-12-31_jb.xml",
    "doc_key": "093450_9290xxxx_..._XML",
    "dokumentart": { "code": "48", "text": "Jahresabschluss" },
    "content_type": "application/xml",
    "format": "jab40",                       // or "legacy_finanzonline" | "pdf"
    "gkl": "M",
    "stichtag": "2025-12-31",
    "eingereicht": "2026-04-10",
    "bytes": 51234,
    "pdf_sibling": {                          // every filing's PDF is stored too
      "blob_path": "raw/093450b/2025-12-31/093450b_2025-12-31_jb.pdf",
      "doc_key": "093450_9290xxxx_..._PDF",
      "content_hash": "sha256:7e11ab…c0",
      "bytes": 142880
    }
  },
  "_meta": {
    "doc_id": "b1f0c4e2-2d77-4a90-9c1a-0e4a2f7c11aa",
    "entity_id": "093450b/2025-12-31",
    "stage": "raw",
    "producer": "ingest@1.0.0",
    "source": "justizonline_firmenbuch_hvd",
    "source_endpoint": "urkunde",
    "license": "CC-BY-4.0",
    "run_id": "2026-06-16-daily-0003",
    "content_hash": "sha256:9f2adb…e7",       // hash of the raw XML bytes
    "timestamps": { "ingested_at": "2026-06-16T05:00:12Z" },
    "lineage": []                              // root of the chain
  }
}
```

### Stage 1 — `parsed` (one filing, normalized to canonical schema)

```jsonc
{
  "filing": {
    "fnr": "093450b",
    "stichtag": "2025-12-31",
    "gj": { "beginn": "2025-01-01", "ende": "2025-12-31" },
    "currency": "EUR",
    "format": "jab40",
    "parsed": true,
    "has_bilanz": true,
    "has_guv": true,
    "bilanz": {
      "bilanzsumme": 23492979.69, "eigenkapital": 6393383.95, "verbindlichkeiten": 9749100.41,
      "anlagevermoegen": 3425134.31, "umlaufvermoegen": 19454205.89, "sachanlagen": 3171182.13,
      "finanzanlagen": 235800.0, "vorraete": 8110619.17, "forderungen": 6043736.82,
      "cash": 5299849.90, "rueckstellungen": 7261735.04, "stammkapital": 218000.0,
      "kapitalruecklagen": 92621.33, "gewinnruecklagen": 265611.24, "bilanzgewinn_verlust": 5817151.38
    },
    "guv": {
      "revenue_basis": "rohergebnis", "rohergebnis": 24424100.52, "personalaufwand": -17070434.71,
      "abschreibungen": -834467.61, "ebit": 1523815.88, "ebitda": 2358283.49, "jahresueberschuss": 1214303.44
    },
    "employees": 95,                          // (verify) JAb 4.0 element; legacy = HGB_Form_3_16/ANZAHL
    "signatory": {                            // raw personal data; GDPR-gated at present stage
      "first_name": "Max", "last_name": "Mustermann", "birth_year": 1972, "signed_at": "2026-04-09"
    }
  },
  "field_provenance": {
    "format": "jab40",
    "mapping_version": "1.0",
    "scaling": { "wert_tsd_applied": false },
    "map": {                                  // canonical_field -> source XML path  (the "source_field" provenance)
      "bilanz.bilanzsumme":        "UEBERMITTLUNG/BILANZ/BILANZ_AKTIVA",
      "bilanz.eigenkapital":       "UEBERMITTLUNG/BILANZ/BILANZ_EIGENKAPITAL",
      "bilanz.verbindlichkeiten":  "UEBERMITTLUNG/BILANZ/BILANZ_VERBINDLICHKEITEN",
      "bilanz.sachanlagen":        "UEBERMITTLUNG/BILANZ/BILANZ_SACHANLAGEN",
      "bilanz.cash":               "UEBERMITTLUNG/BILANZ/BILANZ_KASSENBESTAND_…",   // (verify leaf)
      "guv.rohergebnis":           "UEBERMITTLUNG/GUV_GKV/…/ROHERGEBNIS",
      "guv.jahresueberschuss":     "UEBERMITTLUNG/GUV_GKV/…/JAHRESUEBERSCHUSS",
      "employees":                 "UEBERMITTLUNG/ALLGEMEINE_ANGABEN/…/ARBEITNEHMER"  // (verify)
    }
  },
  "_meta": {
    "doc_id": "7c3d9a51-44b2-4f0e-9bb1-1c9d2a55e004",
    "entity_id": "093450b/2025-12-31",
    "stage": "parsed",
    "producer": "parse@1.0.0",
    "schema_version": "1.0",
    "run_id": "2026-06-16-daily-0003",
    "content_hash": "sha256:4b8cf2…91",
    "checks": { "aktiva_equals_passiva": true, "wert_tsd_applied": false },
    "timestamps": { "ingested_at": "2026-06-16T05:00:12Z", "parsed_at": "2026-06-16T05:00:18Z" },
    "lineage": [
      { "stage": "raw", "doc_id": "b1f0c4e2-2d77-4a90-9c1a-0e4a2f7c11aa",
        "content_hash": "sha256:9f2adb…e7", "created_at": "2026-06-16T05:00:12Z", "producer": "ingest@1.0.0" }
    ]
  }
}
```

### Stage 2 — `consolidated` (all filings + master data merged; fan-in)

```jsonc
{
  "identity": { "fnr": "093450b", "register_id": "AT_093450b", "name": "Schubert CleanTech GmbH",
                "legal_form": "gmbh", "status": "active", "court": { "code": "007", "name": "Handelsgericht Wien" } },
  "location": { "country": "AT", "bundesland": "N", "city": "Ober-Grafendorf", "postal_code": "3200", "street": "…" },
  "company": { "stammkapital": { "amount": 218000, "currency": "EUR" },
               "first_filing_year": 2020, "last_filing_year": 2025, "filing_years_available": 6,
               "founded_year": null, "founded_source": null,        // northdata-only → deferred
               "description": "…(Geschäftszweig from auszug, if present)…" },
  "financials": {
    "currency": "EUR", "latest_year": 2025,
    "has_bilanz": true, "has_guv": true, "has_guv_latest": true, "guv_years": [2020,2021,2022,2023,2024,2025],
    "has_xml": true, "has_pdf_only": false, "revenue_basis": "rohergebnis",
    "completeness": { "2024": { "bilanz_items": 14, "guv_items": 6 }, "2025": { "bilanz_items": 14, "guv_items": 6 } },
    "bilanz": {
      "bilanzsumme":      { "history": { "2020": 10777460.87, "2021": 12890888.66, "2022": 15483505.72,
                                          "2023": 19923837.84, "2024": 21330383.35, "2025": 23492979.69 } },
      "eigenkapital":     { "history": { "2020": 3718917.94, "…": 0, "2025": 6393383.95 } },
      "verbindlichkeiten":{ "history": { "2020": 3780549.12, "…": 0, "2025": 9749100.41 } }
      // … all other canonical Bilanz items, same shape …
    },
    "guv": {
      "rohergebnis":      { "history": { "2020": 13134525.88, "…": 0, "2025": 24424100.52 } },
      "jahresueberschuss":{ "history": { "2020": 572194.45, "…": 0, "2025": 1214303.44 } }
    },
    // Strict no-loss superset (Part B): `positions` keys EVERY recognized canonical (full
    // 317 taxonomy) by canonical name — the typed bilanz/guv above are an ergonomic VIEW of it.
    // `passthrough` keys every UNKNOWN source code. Nothing recognized OR unknown is reduced.
    "positions": {
      "aktiva":                 { "history": { "2025": 23492979.69 }, "source_codes": ["HGB_224_2"], "paragraph_ref": "§224 Abs 2" },
      "immaterielle_vermoegensgegenstaende": { "history": { "2025": 12345.0 }, "source_codes": ["HGB_224_2_A_I"], "paragraph_ref": "§224 Abs 2 A I" }
      // … every other canonical the parser found, with its full year history …
    },
    "passthrough": {
      // "XXX_999_…": { "history": { "2025": 1.0 }, "source_codes": ["XXX_999_…"] }   // unknown codes, never dropped
    }
  },
  "employees": { "history": { "2024": 91, "2025": 95 } },
  "management": {                              // retained internally; gated at present stage
    "primary_gf": { "first_name": "Max", "last_name": "Mustermann", "birth_year": 1972 },
    "n_signatories_latest": 1,
    "signatories_history": { "history": { "2020": 1, "…": 1, "2025": 1 }, "stability_years": 5 }
  },
  "filings": [
    { "stichtag": "2025-12-31", "format": "jab40",               "parsed": true, "gkl": "M",
      "eingereicht": "2026-04-10", "doc_key": "…_XML", "document_url": "https://…/093450b/2025", "pdf_doc_key": "…_PDF" },
    { "stichtag": "2024-12-31", "format": "legacy_finanzonline", "parsed": true, "gkl": "M",
      "eingereicht": "2025-04-15", "doc_key": "…_XML", "document_url": "https://…/093450b/2024", "pdf_doc_key": "…_PDF" }
    // … 2023 … 2020 …
  ],
  "events": [ { "date": "2022-12-02", "type": "name_change", "description": "vormals: Schubert Elektroanlagen GmbH …" } ],
  "_meta": {
    "doc_id": "e90a7733-9f1c-4d2b-bb6e-2a0f4471c2da",
    "entity_id": "093450b",
    "stage": "consolidated",
    "producer": "consolidate@1.0.0",
    "schema_version": "1.0",
    "run_id": "2026-06-16-daily-0003",
    "data_version": 7,
    "content_hash": "sha256:1d77ac…42",
    "checks": { "all_inputs_present": true, "prior_year_reconciled": true },
    "timestamps": { "ingested_at": "2026-06-16T05:00:12Z", "parsed_at": "2026-06-16T05:00:18Z",
                    "consolidated_at": "2026-06-16T05:00:25Z" },
    "supersedes": { "doc_id": "c2b1…(prev)", "content_hash": "sha256:aa10b9…7f",
                    "data_version": 6, "built_at": "2025-05-02T05:00:20Z" },   // version chain
    "inputs": [                                  // every parsed filing + master extract that fed this build
      { "stage": "parsed", "entity_id": "093450b/2025-12-31", "doc_id": "7c3d9a51-44b2-4f0e-9bb1-1c9d2a55e004",
        "content_hash": "sha256:4b8cf2…91", "created_at": "2026-06-16T05:00:18Z" },     // ← the new 2025 filing
      { "stage": "parsed", "entity_id": "093450b/2024-12-31", "doc_id": "9a01…",
        "content_hash": "sha256:55ee…",  "created_at": "2025-05-02T05:00:14Z" },
      { "stage": "master", "source": "auszug", "doc_id": "f7c2…",
        "content_hash": "sha256:3b9d…",  "created_at": "2026-06-16T05:00:13Z" }
    ]
  }
}
```

### Stage 3 — `derived` (adds ratios, growth, trends, percentiles + a `derivations` catalog)

Only the *added/expanded* parts are shown; identity/location/financials carry over from `consolidated`. Note every metric now uses the **uniform metric object** shape.

```jsonc
{
  "financials": {
    "bilanz": {
      "bilanzsumme": {
        "latest": 23492979.69, "latest_year": 2025,
        "history": { "2020": 10777460.87, "…": 0, "2025": 23492979.69 },
        "source_codes": ["HGB_224_2"], "paragraph_ref": "§224 Abs 2",  // Part A: official UGB code + §-ref on every line item
        "annual_growth_rates": { "2021": 0.1961, "…": 0, "2025": 0.1014 },
        "growth_1y": 0.1014, "growth_3y_cagr": 0.1491, "growth_5y_cagr": 0.1686,
        "growth_volatility": 0.0864, "trend": "growing"
      }
      // … same shape for every line item; a jab40 filing would show "source_codes":["AKTIVA"] with the same "§224 Abs 2" ref.
      // If a canonical's code differs across years, "source_codes_by_year": {"2023":["HGB_224_2"], "2024":["AKTIVA"]} is also set.
    }
  },
  "ratios": {
    "equity_ratio": { "latest": 0.2721, "history": { "2020": 0.3451, "…": 0, "2025": 0.2721 },
                      "avg_3y": 0.2750, "avg_5y": 0.2965, "min_5y": 0.2703, "max_5y": 0.3365,
                      "trend": "declining", "volatility": 0.0303 },
    "debt_to_equity": { "latest": 1.5249 },
    "net_margin": { "latest": 0.0497, "avg_5y": 0.0526, "trend": "improving" },
    "roa": { "latest": 0.0517 }, "roe": { "latest": 0.1899 },
    "capital_profile": "balanced"
  },
  "growth": { "profile": "fast_growing", "method": "rohergebnis" },
  "size": { "gkl": "M", "bilanzsumme_band": "medium",
            "peer_percentiles": { "bilanzsumme": 82.9, "equity_ratio": 28.9, "bilanzsumme_5y_cagr": 82.2 } },
  "derivations": {                             // one-time catalog, NOT per value
    "metrics_version": "1.0",
    "formulas": {
      "ratios.equity_ratio":   "eigenkapital / bilanzsumme",
      "ratios.debt_to_equity": "verbindlichkeiten / eigenkapital",
      "ratios.roa":            "jahresueberschuss / bilanzsumme",
      "growth.profile":        "rule(bilanzsumme_5y_cagr, rohergebnis_5y_cagr)",
      "size.peer_percentiles": "percentile_rank(metric, cohort=sector×size_band)"
    }
  },
  "_meta": {
    "doc_id": "44ff1290-7ac3-4e51-8d22-9b3a51c7e6b1",
    "entity_id": "093450b", "stage": "derived", "producer": "derive@1.0.0",
    "schema_version": "1.0", "metrics_version": "1.0", "run_id": "2026-06-16-daily-0003",
    "data_version": 7, "content_hash": "sha256:88c0de…13",
    "timestamps": { "ingested_at": "2026-06-16T05:00:12Z", "parsed_at": "2026-06-16T05:00:18Z",
                    "consolidated_at": "2026-06-16T05:00:25Z", "derived_at": "2026-06-16T05:00:31Z" },
    "lineage": [
      { "stage": "consolidated", "doc_id": "e90a7733-9f1c-4d2b-bb6e-2a0f4471c2da",
        "content_hash": "sha256:1d77ac…42", "created_at": "2026-06-16T05:00:25Z", "producer": "consolidate@1.0.0" }
    ]
  }
}
```

### Stage 4 — `presented` (what the MCP serves; scoped + personal data gated + trimmed public provenance)

```jsonc
{
  "schema_version": "1.0",
  "identity": { "fnr": "093450b", "register_id": "AT_093450b", "name": "Schubert CleanTech GmbH",
                "legal_form": "gmbh", "status": "active" },
  "location": { "bundesland": "N", "city": "Ober-Grafendorf", "postal_code": "3200" },
  "company": { "stammkapital": { "amount": 218000, "currency": "EUR" },
               "first_filing_year": 2020, "last_filing_year": 2025, "filing_years_available": 6,
               "description": "…" },
  "size": { "gkl": "M", "bilanzsumme_band": "medium", "peer_percentiles": { "bilanzsumme": 82.9, "equity_ratio": 28.9 } },
  "financials": { "latest_year": 2025, "currency": "EUR",
                  "has_bilanz": true, "has_guv": true, "has_xml": true, "has_pdf_only": false,
                  "revenue_basis": "rohergebnis",
                  "bilanz": { "bilanzsumme": { /* uniform metric object */ }, "…": {} },
                  "guv": { "rohergebnis": { /* … */ }, "jahresueberschuss": { /* … */ } } },
  "ratios": { "equity_ratio": { /* … */ }, "capital_profile": "balanced" },
  "growth": { "profile": "fast_growing", "method": "rohergebnis" },
  "employees": { "latest": 95, "history": { "2024": 91, "2025": 95 } },
  "filings": [
    { "stichtag": "2025-12-31", "format": "jab40", "parsed": true, "gkl": "M",
      "document_url": "https://…/093450b/2025" }   // PDF/XML always linkable, incl. PDF-only filings
  ],
  "events": [ { "date": "2022-12-02", "type": "name_change", "description": "…" } ],

  // name withheld (gated); age + birth_year (year only) ARE exposed; no month/day/name
  "management": { "n_signatories_latest": 1, "signatories_stable_years": 5,
                  "primary_manager": { "age_at_signing": 53.6, "birth_year": 1972, "role_label": "Geschäftsführer" } },

  // reserved, null in v1
  "sector": null, "enrichment": null, "score": null, "summary": null, "observations": null,

  // trimmed PUBLIC provenance — not the internal hash chain
  "provenance": {
    "source": "Österreichisches Firmenbuch / BMJ – Justiz",
    "license": "CC-BY-4.0",
    "attribution": "Quelle: Österreichisches Firmenbuch / BMJ – Justiz, CC BY 4.0",
    "data_version": 7,
    "built_at": "2026-06-16T05:00:34Z",
    "schema_version": "1.0"
  },

  // internal _meta still stored in Cosmos (not necessarily returned to clients)
  "_meta": {
    "doc_id": "0b5e8e74-1f33-4f7a-bf0a-7790c0a4e2d9",
    "entity_id": "093450b", "stage": "presented", "producer": "present@1.0.0",
    "run_id": "2026-06-16-daily-0003", "data_version": 7, "content_hash": "sha256:af34bd…77",
    "timestamps": { "ingested_at": "2026-06-16T05:00:12Z", "parsed_at": "2026-06-16T05:00:18Z",
                    "consolidated_at": "2026-06-16T05:00:25Z", "derived_at": "2026-06-16T05:00:31Z",
                    "presented_at": "2026-06-16T05:00:34Z" },
    "lineage": [
      { "stage": "derived", "doc_id": "44ff1290-7ac3-4e51-8d22-9b3a51c7e6b1",
        "content_hash": "sha256:88c0de…13", "created_at": "2026-06-16T05:00:31Z", "producer": "derive@1.0.0" }
    ]
  }
}
```

---

## Part 4 — How to read the chain (verification checklist)

- **UUID chain:** `presented.lineage[derived].doc_id` = `derived._meta.doc_id` → `derived.lineage[consolidated].doc_id` = `consolidated._meta.doc_id` → `consolidated.inputs[parsed].doc_id` = `parsed._meta.doc_id` → `parsed.lineage[raw].doc_id` = `raw._meta.doc_id`. Unbroken.
- **Hash integrity:** each lineage entry repeats the upstream `content_hash`, so any upstream change is detectable downstream.
- **Timestamps:** accumulate left-to-right (`ingested_at` … `presented_at`), all `…Z`.
- **source_field:** present at `parsed.field_provenance.map`; formulas at `derived.derivations.formulas`.
- **New-filing handling:** `consolidated._meta.supersedes` points at the prior version's hash; `consolidated._meta.inputs[]` shows the new 2025 parsed filing alongside the older ones; `data_version` bumped 6→7.
- **Personal data:** real names live in `parsed`/`consolidated`/`derived` (internal) but are **gated** in `presented` (age band + counts only) per §5.
- **Attribution:** CC BY 4.0 credit travels to the public `provenance` block.

---

## Part 5 — Open items to finalize these into hard fixtures
1. One real **`auszug` Kurzinformation** XML → lock `location`, `company.description`, and the `management`/person fields (and confirm what's GDPR-sensitive).
2. One real **JAb 4.0** XML filing → finalize the `field_provenance.map` leaf paths and the employee element.
3. Confirm the **cash** and **employees** element paths in both formats (legacy is known: `HGB_224_2_B_IV`, `HGB_Form_3_16/ANZAHL`).
