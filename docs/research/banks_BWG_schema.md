# Extending the Firmenbuch pipeline to Austrian banks (BWG)

Research report for the `agentic-firmenbuch` project. Covers the legal, schema, filing-format, ratio, and detection aspects of adding **Kreditinstitute** (Austrian credit institutions filing under the *Bankwesengesetz*, BWG) to a pipeline currently built around UGB-bilanzing companies.

## 1. Legal basis

Austrian banks prepare their annual statements under a *separate, parallel* accounting regime to the UGB. The key sections live in **BWG XIII. Abschnitt — "Rechnungslegung"**, roughly **§§ 43–65 BWG**:

- **§ 43 BWG** — general clause. Mandates that the balance sheet and P&L of every credit institution (and Kreditinstitute-Verbund), with the exception of *Bausparkassen*, be prepared **according to the format sheets in Anlage 1 (Bilanz) and Anlage 2 (GuV) zu § 43 BWG**. Further sub-itemisation is only permitted to avoid ambiguity or where other law requires it ([RIS BWG § 43](https://www.ris.bka.gv.at/NormDokument.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10004827&Paragraf=43); [JUSLINE § 43 BWG](https://www.jusline.at/gesetz/bwg/paragraf/43)).
- **§§ 44–53 BWG** — bewertungs- and position-specific rules (debt securities of public issuers, money-market paper, bonds, equities, lending positions, intangibles, tangibles, holdings in affiliates, provisions, etc.).
- **§§ 55–58 BWG** — handelsbuch / trading book valuation, derivatives, currency translation.
- **§ 59 BWG** — Kreditinstitute-Verbund / group accounting (consolidation rules for the cooperative sectors).
- **§§ 60–63 BWG** — Lagebericht specifics, Anhang, prudential disclosures.
- **§ 65 BWG** — Prüfung (audit obligations, bankprüfer).
- **Anlage 1 zu § 43 BWG** — Bilanz-Formblatt (rigid line-item ordering).
- **Anlage 2 zu § 43 BWG** — GuV-Formblatt (rigid line-item ordering, including *Zinsen und ähnliche Erträge*, *Nettozinsertrag*, *Provisionsergebnis*, *Handelsergebnis*, *Wertberichtigungen*).

Relation to **UGB §§ 198–243**: the UGB rules apply *subsidiär* — wherever BWG and its Anlagen are silent, UGB rules (recognition, going concern, prudence, valuation principles, Anhang content) fill the gap. But the **structure / Gliederung** of the Bilanz and GuV is fully replaced by BWG Anlagen 1 and 2; the UGB §§ 224 (Bilanz-Gliederung) and 231 (GuV-Gliederung) do *not* apply to banks. Disclosure under §§ 277–281 UGB (Firmenbuch filing) still applies — banks must file with the Firmenbuch — but the *content* follows BWG ([WKO procedure description](https://www.wko.at/oe/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/uebermittlung-jahresabschluesse-verfahrensbeschreibung.pdf)).

**Directive 86/635/EEC** ("Bank Accounts Directive") **is the EU root** of all of the above: it harmonises bank annual accounts across the EU, and BWG §§ 43 ff. plus its Anlagen are the Austrian transposition. So yes, it is relevant — it is the *reason* the BWG structure looks the way it does, and the position taxonomy in Anlage 1/2 maps almost line-for-line to Articles 4 and 27 of the directive.

## 2. Schema specifics

### Balance-sheet positions that differ from UGB

The BWG Anlage 1 schema is *fundamentally different* from the UGB schema — it is not "UGB + extras". Headline differences:

**Aktiva** (key bank-specific items, ordered roughly by liquidity, not by Anlage- vs. Umlaufvermögen):
1. Kassenbestand, Guthaben bei Zentralnotenbanken
2. Schuldtitel öffentlicher Stellen und Wechsel, die zur Refinanzierung bei Zentralnotenbanken zugelassen sind
3. **Forderungen an Kreditinstitute** (a) täglich fällig, (b) sonstige
4. **Forderungen an Kunden**
5. Schuldverschreibungen und andere festverzinsliche Wertpapiere (a) öffentlicher Emittenten, (b) anderer Emittenten
6. Aktien und andere nicht festverzinsliche Wertpapiere
7. Beteiligungen
8. Anteile an verbundenen Unternehmen
9. Immaterielle Vermögensgegenstände / Sachanlagen
10. Eigene Aktien / sonstige Vermögensgegenstände / Rechnungsabgrenzung

**Passiva**:
1. **Verbindlichkeiten gegenüber Kreditinstituten** (a) täglich fällig, (b) mit vereinbarter Laufzeit
2. **Verbindlichkeiten gegenüber Kunden** (a) Spareinlagen, (b) sonstige
3. Verbriefte Verbindlichkeiten (begebene Schuldverschreibungen, andere)
4. Sonstige Verbindlichkeiten / Rechnungsabgrenzung / Rückstellungen
5. **Nachrangkapital / Ergänzungskapital / Partizipationskapital** (the multi-tier capital structure unique to banks)
6. Eigenkapital (gezeichnetes Kapital, Kapitalrücklagen, Gewinnrücklagen, Haftrücklage, Bilanzgewinn)

Off-balance-sheet items are reported separately on the face of the Bilanz: **Eventualverbindlichkeiten** (Akzepte, Indossamentverbindlichkeiten, Bürgschaften, Garantien) and **Kreditrisiken** (unwiderrufliche Kreditzusagen).

### P&L positions

BWG Anlage 2 (Staffelform) follows a *bank-economic* logic, not the cost-of-sales / Gesamtkostenverfahren split of UGB:

1. Zinsen und ähnliche Erträge
2. Zinsen und ähnliche Aufwendungen
3. **Nettozinsertrag** (subtotal)
4. Erträge aus Wertpapieren und Beteiligungen
5. Provisionserträge
6. Provisionsaufwendungen
7. **Provisionsergebnis** (subtotal)
8. Erträge/Aufwendungen aus Finanzgeschäften (**Handelsergebnis**)
9. Sonstige betriebliche Erträge
10. **Betriebserträge** (subtotal)
11. Allgemeine Verwaltungsaufwendungen (Personal, Sachaufwand)
12. Abschreibungen auf Sachanlagen und immaterielle Vermögensgegenstände
13. Sonstige betriebliche Aufwendungen
14. **Betriebsaufwendungen** (subtotal)
15. **Betriebsergebnis** (subtotal — analogue of EBIT but bank-specific)
16. **Wertberichtigungen auf Forderungen und Zuführungen zu Rückstellungen für Eventualverbindlichkeiten und Kreditrisiken** (risk costs / loan-loss provisions)
17. Erträge aus der Auflösung von Wertberichtigungen
18. Wertberichtigungen auf Wertpapiere des Finanzanlagevermögens
19. **Ergebnis der gewöhnlichen Geschäftstätigkeit**
20. Außerordentliches Ergebnis
21. Steuern / Jahresüberschuss / Bilanzgewinn

There is **no "EBIT" or "Umsatzerlöse"** line — both are meaningless for banks (interest income is not "revenue" in the goods/services sense).

### Size classes

The UGB *kleinst/klein/mittelgroß/groß* classes (§ 221 UGB) do **not** drive bank disclosure. Banks have **prudential size classes** under CRR / SSM:
- **G-SII** (global systemically important — Austria has none currently),
- **O-SII** (other systemically important — designated by FMA, currently RBI, Erste Group, UniCredit Bank Austria, Raiffeisen-Landesbanken-Holding, BAWAG ([FMA O-SII designation](https://www.fma.gv.at/banken/))),
- **Large institutions** (≥ €30 bn total assets, CRR Art. 4(1)(146)),
- **Small and non-complex institutions (SNCI)** (≤ €5 bn, CRR Art. 4(1)(145)),
- **LSI** (Less Significant Institution, supervised directly by FMA/OeNB rather than ECB SSM).

These class drive Pillar-3 disclosure depth, not Firmenbuch filing volume.

## 3. JustizOnline / Jahresabschluss filing format for banks

**Critical finding — banks do NOT file structured XML at the Firmenbuch.** They file **PDF**.

The official WKO procedure document for JAb 4.0 explicitly notes: *"Eine Einreichung als PDF ist nur ausnahmsweise zulässig, wenn die Einreichung in den vorgegebenen Formaten nicht möglich ist"* — and lists banks and insurance companies as the live exception cases because **no structured XML schema variant exists for the BWG Anlage 1/2 line items** ([WKO JAb 4.0 PDF](https://www.wko.at/oe/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/uebermittlung-jahresabschluesse-verfahrensbeschreibung.pdf); [WKO Übermittlung ab 1.1.2026](https://www.wko.at/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/uebermittlung-der-bilanzen-an-das-firmenbuch-finanzonline)).

Concretely:
- JAb 4.0 (XML, mandatory from 1.1.2026 for everyone else) has a single taxonomy that maps to UGB Bilanz/GuV positions. There is **no `Anlage 1 zu § 43 BWG` variant** in the schema. The BMJ summary of changes ([BMJ JAb 4.0 changes](https://www.bmj.gv.at/dam/jcr:1d3d13d6-fba4-4ddd-8383-22b3669ca1d1/Zusammenfassung%20der%20%C3%84nderungen%20JAb%204.0.pdf)) does not introduce one.
- The official XSDs for JAb 4.0 are hosted by BMJ / JustizOnline (`justizonline.gv.at`); these cover the UGB taxonomy only. No bank XSD is published there as of June 2026.
- Banks file the same **§§ 277–281 UGB** package (Jahresabschluss, Lagebericht, Bestätigungsvermerk) at the Firmenbuch — but as **PDF attachments** through ERV / JustizOnline. The HVD / `auszug` payload for a bank will therefore include `dokument`-typed entries pointing at PDFs, not structured `jb`-typed entries.

**For the pipeline, this means the existing JAb 4.0 parser will NOT work on banks.** Either:
1. add an OCR / layout-extraction stage for the PDF Anlage 1/2 (high effort, brittle), or
2. ingest from a *secondary* structured source (see below).

The structured source for bank line-items is **prudential supervisory reporting**, not Firmenbuch:
- **FINREP** (financial reporting) and **COREP** (capital reporting) are XBRL filings to FMA/OeNB under EBA ITS. These contain the Anlage 1/2 positions with much greater granularity, but they are **supervisory, not public** — only aggregated statistics are released by OeNB.
- **Pillar 3 disclosures** (CRR Part Eight) **are public**. Under CRR III (Art. 433–434a), the **EBA Pillar 3 Data Hub (P3DH)** centralises these for all EEA institutions, operational from 2025 for large/other institutions, 2026 for SNCIs ([EBA P3DH](https://www.eba.europa.eu/risk-and-data-analysis/pillar-3-data-hub); [EBA ITS final report 02/2025](https://www.eba.europa.eu/sites/default/files/2025-02/fe98daa0-231d-4e92-b8a4-f1ad42d257e6/EBA_ITS_2025_01%20Final%20Report%20P3DH%20ITS_large%20and%20other%20institutions.pdf)). P3DH publishes standardised XBRL templates that *do* expose CET1, RWA, NPL, LCR, etc. on a per-institution basis. **This is the right machine-readable source for bank prudential metrics.**

The FMA also publishes an **"Anlage A1 Jahresabschluss unkonsolidiert Kreditinstitute gemäß § 1 BWG"** template ([FMA download d=1230](https://www.fma.gv.at/wp-content/plugins/dw-fma/download.php?d=1230)) used for supervisory submission — the file is access-restricted to the FMA portal; only the template structure is public-facing.

## 4. Ratios that make sense for banks

UGB ratios that **do not apply** to banks:
- **Eigenkapitalquote** (UGB-style equity/total-assets) is meaningless — banks are leverage businesses; CET1 / total RWA is the right denominator.
- **EBIT, EBITDA** — no operating-vs-financial split exists; everything is financial.
- **ROA** in the conventional sense — replaced by *Return on Assets* on a risk-weighted basis.
- **Current ratio / Quick ratio** — replaced by **LCR** (Liquidity Coverage Ratio) and **NSFR** (Net Stable Funding Ratio).
- **Umsatz-based ratios** — no Umsatz.
- **Gearing / Verschuldungsgrad** — replaced by **Leverage Ratio** (CRR Art. 429).

Bank-relevant ratios to compute:

| Ratio | Formula | Data source |
|---|---|---|
| **CET1 ratio** | CET1 capital / RWA | Pillar 3 (not in Firmenbuch JA) |
| **Total Capital Ratio** | Own funds / RWA | Pillar 3 |
| **Leverage Ratio** | Tier 1 / total exposure | Pillar 3 |
| **NPL ratio** | Non-performing loans / total loans | Pillar 3 (FINREP F18) |
| **NIM** (Net Interest Margin) | Nettozinsertrag / Ø interest-bearing assets | BWG Anlage 2 + Anlage 1 averages — **computable from Firmenbuch JA PDF** |
| **Cost-Income Ratio** | Betriebsaufwendungen / Betriebserträge | BWG Anlage 2 — **computable from JA** |
| **RWA density** | RWA / total assets | Pillar 3 + JA |
| **Loan-to-Deposit Ratio** | Forderungen an Kunden / Verbindlichkeiten geg. Kunden | BWG Anlage 1 — **computable from JA** |
| **Risk cost ratio** | Wertberichtigungen / Ø Forderungen an Kunden | BWG Anlage 2 — **computable from JA** |
| **ROE** | Jahresüberschuss / Ø Eigenkapital | BWG Anlage 1/2 |
| **LCR / NSFR** | regulatory liquidity buffers | Pillar 3 only |

NIM, CIR, LDR, ROE and risk-cost ratio are computable from BWG Anlage 1/2 alone. CET1, leverage, NPL, LCR, NSFR require Pillar 3 / P3DH.

## 5. Detection heuristics

To flag "this is a bank" from Firmenbuch metadata alone, use a layered classifier (highest precision first):

1. **FMA Konzessionsregister cross-check** (authoritative). The FMA `Unternehmensdatenbank` lists every licensed Kreditinstitut. ([FMA company DB](https://www.fma.gv.at/unternehmensdatenbank-suche/); [English version](https://www.fma.gv.at/en/search-company-database/)) — see § 6 for the machine-readable feed.
2. **OeNB Bankstellenverzeichnis** (authoritative, daily CSV) — every BLZ (bank code) maps to a Firmenbuch number ([Bankstellenverzeichnis](https://www.oenb.at/Statistik/Klassifikationen/Bankstellenverzeichnis.html)).
3. **ÖNACE-Code**: primary classifier is **64.19** "Kreditinstitute (ohne Spezialkreditinstitute)" with sub-classes 64.19.1 Kreditbanken, 64.19.2 Sparkassensektor, 64.19.3 Genossenschaftssektor; plus **64.11** Zentralbanken and **64.92** Sonstige Kreditgewährung ([ÖNACE 2008](https://www.statistik.at/kdb/downloads/pdf/prod/OENACE2008_DE_CTE.pdf)). Caveat: ÖNACE in the Firmenbuch is self-declared and frequently stale.
4. **Rechtsform**: AG (Aktienbanken, Sparkassen-AG, Hypo-AG), eGen (Raiffeisen, Volksbanken), Sparkasse (legal form sui generis). GmbH banks exist but are rare. **eGen alone is not sufficient** — most eGen are non-banks.
5. **Name regex**: `(?i)\b(bank|sparkasse|raiffeisen|volksbank|hypo|bausparkasse|kreditinstitut)\b` — high recall, decent precision but produces false positives (e.g. "Hypothekenmakler", "Bankhaus Müller GmbH Steuerberatung").
6. **Geschäftszweig text** in the Firmenbuchauszug — contains "Bankgeschäfte iSd § 1 BWG" or specific § 1 Abs. 1 Z … reference. High precision when present.

**Recommended pipeline rule**: a company is a bank iff its FN appears in the OeNB Bankstellenverzeichnis CSV (joined on FN ↔ BLZ ↔ Firmenbuchnummer). Fallback rules 3–6 only for backfill of FNs not yet in the OeNB file.

**Count of Austrian banks**: as of the most recent OeNB structural data (Q1 2026), **roughly 440–460 main credit institutions** (Hauptanstalten) operate in Austria; the "3,400" figure that appears in OeNB tables includes ~3,000 branches (Zweigstellen) of those same Hauptanstalten ([OeNB Anzahl Kreditinstitute Teil 1](https://www.oenb.at/Statistik/Standardisierte-Tabellen/Finanzinstitutionen/kreditinstitute/Strukturdaten/Anzahl-der-Kreditinstitute-nach-Sektoren---Teil-1.html); [OeNB Fakten zu Österreich und seinen Banken, Juli 2025](https://www.oenb.at/Publikationen/Finanzmarkt/Fakten-zu-Oesterreich-und-seinen-Banken.html)). I could not confirm the exact main-institution headcount from the public landing pages (the JS-rendered ISA-Web tables blocked WebFetch); the live CSV download from OeNB will give the exact number.

## 6. Reference material

**Machine-readable lists of Austrian banks**
- **OeNB Bankstellenverzeichnis** (the canonical source): CSV, **updated daily**, plus monthly ZIP archive. Contains all Bankstellen in Austria with BLZ, name, address and the linking Firmenbuchnummer. ([Bankstellenverzeichnis page](https://www.oenb.at/Statistik/Klassifikationen/Bankstellenverzeichnis.html))
- **OeNB Veränderungen Bankenstammdaten** — delta/change log of bank master data, useful for daily-pipeline change-feed parity. ([Veränderungen Bankenstammdaten](https://www.oenb.at/Statistik/Klassifikationen/veraenderungen-bankenstammdaten.html))
- **FMA Unternehmensdatenbank** — searchable, filterable by company category (Banken, Versicherungen, Wertpapierfirmen). I could not find a documented bulk CSV/JSON export; the search UI is the only public access I confirmed. ([FMA company DB](https://www.fma.gv.at/unternehmensdatenbank-suche/))
- **data.gv.at** — OeNB publishes structural bank tables as open data, e.g. ["Anzahl der Kreditinstitute nach Sektoren"](https://www.data.gv.at/katalog/en/dataset/oenb_anzahlvonhauptanstaltenundzweigstellenderkreditinstituteinsterreichnachbundeslndernundbank1).

**Schema definitions**
- **BWG full text** (RIS, authoritative): [ris.bka.gv.at BWG](https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10004827); the Anlagen 1, 2, 5, 6 are linked as separate documents (`Anlage=1`, `Anlage=2`).
- **FMA "Austrian Banking Act" English consolidated version** (helpful for translation work): [fma.gv.at PDF d=468](https://fma.gv.at/wp-content/plugins/dw-fma/download.php?d=468). Note: WebFetch was 403-blocked but the URL serves the PDF in a normal browser.
- **EBA Pillar 3 Data Hub** (primary source for prudential metrics with bank-level granularity): [eba.europa.eu/p3dh](https://www.eba.europa.eu/risk-and-data-analysis/pillar-3-data-hub) plus the [EBA P3DH ITS final report, Feb 2025](https://www.eba.europa.eu/sites/default/files/2025-02/fe98daa0-231d-4e92-b8a4-f1ad42d257e6/EBA_ITS_2025_01%20Final%20Report%20P3DH%20ITS_large%20and%20other%20institutions.pdf).
- **OeNB statistics** for cross-validation of aggregates: [oenb.at Kreditinstitute](https://www.oenb.at/Statistik/Standardisierte-Tabellen/Finanzinstitutionen/kreditinstitute.html), [Kennzahlen österreichischer Banken](https://www.oenb.at/finanzmarkt/bankenaufsicht/kennzahlen-oesterreichischer-banken.html).
- **JAb 4.0 procedure description** (confirms bank PDF carve-out): [WKO PDF](https://www.wko.at/oe/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/uebermittlung-jahresabschluesse-verfahrensbeschreibung.pdf) and the [BMJ summary of changes](https://www.bmj.gv.at/dam/jcr:1d3d13d6-fba4-4ddd-8383-22b3669ca1d1/Zusammenfassung%20der%20%C3%84nderungen%20JAb%204.0.pdf).
- **Directive 86/635/EEC** (EUR-Lex), source of the Anlage 1/2 taxonomy: search EUR-Lex CELEX `31986L0635`.

## Open items / where the answer is not definitive

- The **exact verbatim Anlage 1 and Anlage 2 line ordering** is in RIS and JUSLINE but both pages 503'd / paginated during this research run. Before implementing the parser, fetch `https://www.ris.bka.gv.at/NormDokument.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10004827&Anlage=1` and `…&Anlage=2` in a browser and use the canonical wording. The list above is consolidated from multiple secondary sources (Bank Austria filings, JUSLINE excerpts, BWG commentary) and is accurate at the position level but the exact numbering and sub-letter ordering should be verified against RIS.
- **No publicly documented bank XSD variant for JAb 4.0** was found. Treating "banks file PDF only at the Firmenbuch" as a hard constraint is the safe assumption.
- **Exact current count of Austrian Hauptanstalten** (~440 range) is an interpolation from the OeNB landing page; the live CSV download will resolve this.
- **FMA bulk-export availability** for the Konzessionsregister could not be confirmed. The OeNB Bankstellenverzeichnis CSV is the better-documented machine-readable alternative.
