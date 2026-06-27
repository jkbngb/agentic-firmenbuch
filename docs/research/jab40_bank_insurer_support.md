# JAb 4.0 — Bank & Insurer support

**TL;DR.** JAb 4.0 does **not** support BWG (Kreditinstitute) or VAG
(Versicherungsunternehmen) accounting schemas. The XSD is a pure UGB (HGB-style)
structure. Banks and insurers are explicitly out of scope today and submit their
filings as **PDF via ERV**; structured support is on the BMJ wishlist but
unscheduled. Our pipeline already routes PDF-only filings to the right code path,
so there is no silent data corruption — but financials are simply absent for
those entities.

## 1. What the XSD supports

- **KI (banks): no.** No element, type, or enumeration in
  `docs/reference/jab40_struktur/JAb_4_00-Uebermittlung.xsd` (3414 lines) carries
  BWG-specific semantics. The only hits for "Kreditinstitut" are the ordinary
  UGB Bilanz lines `KASSENBESTAND_SCHECKS_GUTHABEN_BEI_KREDITINSTITUTEN`
  (line 643) and `VERBINDLICHKEITEN_GEGENUEBER_KREDITINSTITUTEN` (line 1013) —
  i.e. positions every UGB-accounted company has, not a separate bank schema.
  Zero occurrences of "BWG", "Anlage 2 BWG", or "Solvenz".
- **Insurers: no.** Zero occurrences of "Versicher", "VAG", or "FMA" anywhere
  in the XSD.
- The root `UEBERMITTLUNG` (lines 12–47) offers exactly one choice — full
  `BILANZ` + optional `GUV_GKV`/`GUV_UKV` (UGB §231), or `ANLAGE1`/`ANLAGE3`
  (UGB Formblatt-VO Anlagen 1/3). No fourth branch for KI/VU.
- `JAHRESABSCHLUSS_KONZERNABSCHLUSS` (lines 147–156) is an enum of exactly
  `{JAB, JAB-ANLAGE12, JAB-ANLAGE32, KAB}` — all UGB. No `RechtsnormVerweis`,
  no `BilanzierungsArt` discriminator, no `AnlageNachBWG`.
- `RECHTSFORM` (lines 287–307) enumerates only generic legal forms
  (AG, EU, GES, KG, OG, GEN, …). There is **no "KI" or "VU" value** that would
  let a filing self-identify as bank or insurer.

## 2. The position taxonomy in the Excel file

`4.00_Struktur_JAb_2026_03_11-V31.xlsx` has 17 sheets:
`Titelblatt, Festlegungen, Gliederung, allg. Angaben, Bilanz, GuV (GKV),
GuV (UKV), Anlage 1, Anlage 3, Anlage 2, Zusatzangaben Mikro-AGs,
Anlagenspiegel, Verbindlichkeitenspiegel, Forderungenspiegel,
Rückstellungenspiegel, Rücklagenspiegel, Prüfungen`.

All sheets reflect **UGB §§ 224–231 + Formblatt-VO Anlagen 1/2/3**. There is no
sheet for "Anlage 2 BWG" (the §43 BWG bank balance template) or for VAG
positions. The "Anlage 2" sheet is the UGB §237 Anhang only.

## 3. Filing reality: what banks/insurers actually upload

The Austrian Wirtschaftskammer's official guidance is explicit:

> "Dieser Weg über den ERV bietet sich auch dann an, wenn der Jahresabschluss
> wegen technischer Unmöglichkeit der strukturierten Einbringung (derzeit etwa
> bei Banken und Versicherungen) als PDF übermittelt werden darf."

— [WKO: Übermittlung der Bilanzen an das Firmenbuch über JustizOnline ab 1.1.2026](https://www.wko.at/branchen/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/Uebermittlung_der_Bilanzen_an_das_Firmenbuch_ueber_Finanzo.html)

The BMJ's official change summary confirms this is a future ambition, not
present capability:

> "Schließlich sollen auch die Besonderheiten für Banken und Versicherungen in
> der Struktur abbildbar sein."

— [BMJ: Zusammenfassung der Änderungen JAb 4.0](https://www.bmj.gv.at/dam/jcr:1d3d13d6-fba4-4ddd-8383-22b3669ca1d1/Zusammenfassung%20der%20%C3%84nderungen%20JAb%204.0.pdf) (p. 1)

So today: **single universal XSD, UGB-only**; banks and insurers fall back to
PDF-only ERV submission. The structured schema is *intended* to grow KI/VU
coverage later but has no concrete release.

## 4. Current pipeline behavior

The parser has no bank/insurer branch:

- `packages/core/src/fbl_core/formats.py:35` — `detect_xml_variant()` returns
  one of `legacy_finanzonline | firmenbuch_2025 | jab40_semantic`. No fourth
  value, and the JAb 4.0 branch keys only on namespace.
- `packages/70_parse/src/fbl_parse/parser.py:192` — `_extract_positions()`
  dispatches on those three variants only.
- `packages/70_parse/src/fbl_parse/parser.py:78` — `parse_pdf_only(fnr,
  stichtag, …)` builds a stub `ParsedFiling(format="pdf", parsed=False,
  has_bilanz=False, has_guv=False)`. **This is the path bank/insurer filings
  actually take**, since ingest sees a PDF in `raw` (no XML to parse). No
  positions are emitted; the document link is preserved. So:
  - **Not silently mis-mapped** (the XML never reaches the position extractor).
  - **Not a hard parse error** (PDF-only is a first-class state).
  - **Data is absent**, not wrong: bilanz/guv are `None` and downstream
    `derive`/`consolidate` will treat the entity as "filing exists, financials
    not structured".
- The §15b-2 guardrail (parser.py:118–129) means that if a bank ever *did* send
  in a JAb 4.0-namespaced XML containing only BWG positions that don't match
  our UGB position map, the result would be a loud dead-letter with
  `error="jab40_semantic: no positions extracted (unhandled schema?)"`. Today
  the realistic risk is zero because such filings don't exist.

## 5. Pragmatic recommendation

- **For V1: do nothing schema-side.** Bank/insurer filings are already handled
  correctly as PDF-only stubs; there is no XML to misparse. Document the
  limitation in the MCP server's response when `has_bilanz=False` for a KI/VU
  Rechtsform.
- **No `core/mapping` extension is useful yet.** The BWG §43/VAG §138 position
  taxonomies aren't in the JAb 4.0 XSD, so there's nothing to map *to*. Adding
  speculative KI/VU canonicals would be dead code.
- **Optional, cheap win:** detect KI/VU at the master-data layer (`auszug`
  Rechtsform / NACE branch K64.19 / K65) and surface a `financials_basis:
  "pdf_only_bwg_or_vag"` flag on the consolidated record, so MCP consumers can
  distinguish "not yet filed" from "structurally unfilable in this format".
- **Watch BMJ change-logs** for a future XSD revision that adds a
  RechtsnormVerweis or a new BILANZ_KI/BILANZ_VU choice — that would be the
  trigger to add a fourth variant and a parallel mapping table. None of the
  current `change-log.md` entries (Versionen 11→31) hint at this work.

**Bottom line:** the schema-level question has a clean negative answer, and our
pipeline's PDF-only path already does the right thing. The work to add real KI/VU
support cannot start until JustizOnline publishes a BWG/VAG schema extension.
