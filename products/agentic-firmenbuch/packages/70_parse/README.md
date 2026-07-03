# `parse` (`fbl_parse`) — Stage 2 · `90_raw` → `70_parsed`

**Layer:** `70_parsed` | **reads:** Blob `90-raw` | **writes:** Blob `70-parsed`

**Purpose:** turn one raw Jahresabschluss filing (XML bytes) into the canonical
[`ParsedFiling`](../core/src/fbl_core/models/filing.py) (§8.4). Pure function of the
input bytes — **idempotent**: identical content yields an identical `content_hash`.

## Inputs → outputs
- **Input:** raw filing bytes from `90_raw` (Blob), any of three XML variants.
- **Output:** a `ParsedFiling` JSON document destined for `70_parsed` (Blob),
  carrying the typed `Bilanz`/`GuV`, the full recognized `positions` map, employees,
  signatories (age + birth-year only), `field_provenance`, quality `checks`, and the
  lineage `_meta` chained back to the raw doc.

**Official code preservation (Part A):** for every recognized position, `position_codes[canonical]`
records the EXACT source identifier it was parsed from — the `HGB_*`/`XXX_*` code (legacy/fb2025) or
the JAb 4.0 element name. Two distinct codes mapping to one canonical are **both kept** (collision
logged; value stays from the first). Unknown codes stay in `field_provenance.passthrough`; the JAb 4.0
value carriers (`POSTENZEILE`/`BETRAG_GJ`/`BETRAG_VJ`) are excluded from passthrough so it holds only
genuine unknown positions. Passthrough also captures **non-`HGB_` free-text slots** (`FREI*`/
`FREIER_SUB_POSTEN`/`GEB_BEFREIUNG`) that filers use for real positions — keyed `CODE: <TEXT>`
(`#n` suffix on clash) so multiple `FREI` rows all survive, never dropped (§5.1, live-validated).

## The three XML variants (§15b-1, auto-detected)
| Variant | Namespace | Value at | Fiscal year |
|---|---|---|---|
| `legacy_finanzonline` | `finanzonline.bmf.gv.at` | `POSTENZEILE/BETRAG` | `GJ` |
| `firmenbuch_2025` | `finanzonline.bmf.gv.at` | `BETRAG_GJ` | `GESCHAEFTSJAHR` |
| `jab40_semantic` | `justiz.gv.at` v4.0 | element's own text | `GESCHAEFTSJAHR` |

The prototype only handled `HGB_*` tags, so a truly semantic JAb 4.0 filing would
be missed — `xml_jab40` extraction closes that gap (§15b-2). Final v4 leaf paths
are confirmed against a real sample (§16 open item 3); the mapping path is tested.

## Edge cases handled (§15b) — all unit-tested
`WERT_TSD = j` ×1000 scaling · `XXX_*`/unknown codes kept in a **passthrough**
(never dropped) · recognized-but-non-model positions preserved in `positions` ·
multi-line name hyphen-gluing · employees (`HGB_Form_3_16/ANZAHL`) · signatory
`age_at_signing` + `birth_year` with **day/month discarded** (GDPR) · `PERS_KENN`
sibling positional fallback · alternate `PERSON/GEBURTSDATUM` birth source ·
malformed `GEB_DAT` → null (no crash) · negative Eigenkapital flagged · Bilanz-only
→ `has_guv=False` · parse error → dead-letter stub with `error` · PDF-only stub.

## Run it standalone
```bash
uv run pytest packages/70_parse        # unit + fixture tests
uv run python -c "from pathlib import Path; from fbl_parse import parse_filing; \
print(parse_filing(Path('tests/fixtures/raw/030435h_2020-03-31_jb.xml').read_bytes()).bilanz)"
```

## Key files
| Path | What |
|---|---|
| `src/fbl_parse/parser.py` | `parse_filing` orchestrator + `parse_pdf_only` |
| `src/fbl_parse/variant.py` | format detection |
| `src/fbl_parse/positions.py` | HGB + v4 position extraction, passthrough, scaling |
| `src/fbl_parse/people.py` | signatories, employees, age feature |
| `src/fbl_parse/xml_common.py` | namespace-aware helpers, name-glue, date parsing |

## Definition of Done (§8.4) — met
Parses the legacy + firmenbuch_2025 + jab40 samples to exact numbers; `XXX_*`/unknown
codes preserved; malformed birth dates don't crash; `age_at_signing` correct;
GuV-presence correct on Bilanz-only fixtures. `ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
**Previous:** `ingest` (`90_raw`, stage 5) · **Next:** `consolidate` (`50_consolidated`,
stage 6). Shared contracts live in [`core`](../core/README.md).

---
↑ [Repo root](../../README.md) · Specs: [Technische](../../docs/specs/Technische_Spezifikation.md) · [Fachliche](../../docs/specs/Fachliche_Spezifikation.md) · [Pipeline samples](../../docs/pipeline-step-samples.md) · Fixtures: [tests/](../../tests/README.md)
