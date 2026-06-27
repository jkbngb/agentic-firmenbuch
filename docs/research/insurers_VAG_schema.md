# Extending the Firmenbuch pipeline to Austrian insurers (VAG 2016)

Research report for the `agentic-firmenbuch` project. Covers the legal, schema, filing-format, ratio, and detection aspects of adding **Versicherungsunternehmen** (Austrian insurers, regulated under the *Versicherungsaufsichtsgesetz 2016* and Solvency II) to a pipeline currently built around UGB-bilanzing companies.

## 1. Legal basis

Insurance accounting lives in the **7. Hauptstück of VAG 2016 — "Rechnungslegung und Konzernrechnungslegung"**, roughly **§§ 136-167 VAG**. The user's initial estimate of §§ 138-148 is in-range but truncated. Key sections:

- **§ 136 VAG** – Anwendungsbereich; declares which UGB rules apply by default for AG / SE / VVaG / Privatstiftung forms.
- **§ 137 VAG** – allgemeine Vorschriften (Jahresabschluss + Lagebericht + CG-Bericht, calendar-year Geschäftsjahr).
- **§ 139 VAG** – delegation to the FMA to issue a Rechnungslegungsverordnung (the **VU-RLV**).
- **§ 140 VAG** – Bilanzabteilungen Leben / Kranken / Schaden-Unfall (technical accounts).
- **§ 143 VAG** – Risikorücklage (0.6 % addition / 4 % cap, separately disclosed).
- **§ 144 VAG** – **Gliederung der Bilanz** (inline layout, Aktiva A-I, Passiva A-J).
- **§ 146 VAG** – **Gliederung der GuV** (Staffelform, inline; three technical accounts I/II/III plus non-technical IV).
- **§§ 148 ff. VAG** – Bewertung.
- **§§ 241-244, 248 VAG** – SFCR (Bericht über Solvabilität und Finanzlage) content and timing.

Sources: [VAG 2016 consolidated (RIS)](https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=20009095), [§ 144 VAG (jusline)](https://www.jusline.at/gesetz/vag_2016/paragraf/144), [§ 146 VAG (jusline)](https://www.jusline.at/gesetz/vag_2016/paragraf/146).

**Anlagen to VAG 2016**: there are only **two Anlagen, A and B** – *not* a six-annex series. **Anlage A** (zu § 7 Abs. 4) holds the 23-class Versicherungszweige catalog (Unfall, Krankheit, Kasko, Feuer, Haftpflicht, Kredit, Kaution, Leben, fondsgebunden, Rückversicherung …). **Anlage B** (zu § 88) holds the Solvency-I-legacy Eigenmittel-formulas. The Bilanz and GuV layouts are not in an Anlage – they sit **inline in §§ 144 and 146** themselves.

**Relation to UGB §§ 198-243**: structural rules are replaced wholesale. § 144 Abs. 4 disapplies § 224 UGB (Bilanz-Gliederung); § 146 Abs. 6 disapplies § 231 UGB (GuV-Gliederung); § 138 Abs. 1 disapplies § 246 UGB. UGB Bewertungsgrundsätze (§§ 201 ff.) apply via § 148 VAG with insurance-specific modifications. Disclosure obligations under §§ 277-281 UGB still apply – insurers must file in the Firmenbuch – but the *content* follows VAG.

**FMA layer – VU-RLV**: the FMA's *Versicherungsunternehmen-Rechnungslegungsverordnung* ([BGBl. II 316/2015](https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=20009320)) fills in details (Kapitalanlagen sub-classification, versicherungstechnische Rückstellungen im Eigenbehalt, Berichte / Formblätter, separate Erfolgsrechnungen per Versicherungszweig). It also prescribes the verbindliche Formblätter.

**EU context**: VAG 2016 transposes [Directive 2009/138/EC (Solvency II)](https://eur-lex.europa.eu/eli/dir/2009/138/oj). The hierarchy is Level 1 = Directive 2009/138/EC → VAG 2016; Level 2 = directly-applicable [Delegated Regulation (EU) 2015/35](https://eur-lex.europa.eu/eli/reg_del/2015/35/oj) (SCR/MCR standard formula, technical provisions, own-funds tiering); Level 3 = EIOPA ITS, notably the public-disclosure ITS now governed by [Implementing Regulation (EU) 2023/895](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32023R0895) (SFCR templates, replacing 2015/2452).

## 2. Schema specifics

### Bilanz (§ 144 VAG) – key insurer-specific positions

```
AKTIVA
A. Immaterielle Vermögensgegenstände (Firmenwert, Erwerb Versicherungsbestand, sonstige)
B. Kapitalanlagen
   I.   Grundstücke und Bauten
   II.  Kapitalanlagen in verbundenen Unt. + Beteiligungen
        (Anteile, Schuldverschreibungen/Darlehen je verb. Unt./Beteiligungen)
   III. Sonstige Kapitalanlagen
        1. Aktien und andere nicht festverzinsliche WP
        2. Schuldverschreibungen + andere festverzinsliche WP
        3. Anteile an gemeinschaftlichen Kapitalanlagen (Fonds)
        4. Hypothekenforderungen
        5. Vorauszahlungen auf Polizzen
        6. Sonstige Ausleihungen
        7. Guthaben bei Kreditinstituten
        8. Andere Kapitalanlagen
   IV.  Depotforderungen aus übernommenem RV-Geschäft
C. Kapitalanlagen der fonds- und indexgebundenen Lebensversicherung
D. Forderungen (direktes VS-Geschäft, RV-Abrechnung, ausstehende Einlagen, sonstige)
E. Anteilige Zinsen und Mieten
F. Sonstige Vermögensgegenstände
G. Verrechnungsposten mit der Zentrale
H. Rechnungsabgrenzungsposten
I. Aktive latente Steuern

PASSIVA
A. Eigenkapital  (Grundkapital, Kapital-/Gewinnrücklagen, Risikorücklage, Bilanzgewinn)
B. Nachrangige Verbindlichkeiten
D. Versicherungstechnische Rückstellungen  (jeweils brutto + Anteil RV)
   I.   Prämienüberträge
   II.  Deckungsrückstellung
   III. Rückstellung für noch nicht abgewickelte Versicherungsfälle
   IV.  Rst. f. erfolgsunabhängige Prämienrückerstattung
   V.   Rst. f. erfolgsabhängige Prämienrückerstattung / Gewinnbeteiligung
   VI.  Schwankungsrückstellung  (nur Schaden/Unfall)
   VII. Sonstige versicherungstechnische Rückstellungen
E. Versicherungstechnische Rst. der fonds-/indexgebundenen LV
F. Nicht-versicherungstechnische Rückstellungen (Abfertigung, Pension, Steuer, latent, sonstige)
G. Depotverbindlichkeiten aus abgegebenem RV-Geschäft
H. Sonstige Verbindlichkeiten
I. Verrechnungsposten mit der Zentrale
J. Rechnungsabgrenzungsposten
```

### GuV (§ 146 VAG) – Staffelform

The GuV has **three separate versicherungstechnische Rechnungen** (I Schaden/Unfall, II Krankenversicherung, III Lebensversicherung), each with substantially the same structure, converging into IV Nicht-versicherungstechnische Rechnung. Schaden/Unfall has the Schwankungsrückstellung-Veränderung; Lebensversicherung adds *Nicht realisierte Gewinne/Verluste aus Kapitalanlagen*.

```
I/II/III. Versicherungstechnische Rechnung – Sparte
 1. Abgegrenzte Prämien (Verrechnete Prämien gesamt − abgegebene RV − Δ Prämienabgrenzung)
 2. Kapitalerträge des technischen Geschäfts
 3. Sonstige versicherungstechnische Erträge
 4. Aufwendungen für Versicherungsfälle (Zahlungen + Δ Rst. für noch nicht abgewickelte VS-Fälle)
 5./6. Erhöhung/Verminderung versicherungstechnische Rückstellungen
 7./8. Aufwendungen f. erfolgsunabhängige / -abhängige Prämienrückerstattung
 9. Aufwendungen für den Versicherungsbetrieb (Abschluss + Verwaltung − Rückversicherungsprovisionen)
10. Sonstige versicherungstechnische Aufwendungen
11. Veränderung der Schwankungsrückstellung  (nur Schaden/Unfall)
12. Versicherungstechnisches Ergebnis der Sparte

IV. Nicht-versicherungstechnische Rechnung
 1. Versicherungstechnisches Ergebnis (Summe I+II+III)
 2. Erträge aus Kapitalanlagen (Beteiligungen, Grundstücke, sonstige, Zuschreibungen, Gewinne aus Abgang)
 3. Aufwendungen für Kapitalanlagen (Verwaltung, Abschreibungen, Zinsaufwand, Verluste aus Abgang)
 4. In versicherungstechnische Rechnung übertragene Kapitalerträge
 5./6. Sonstige nicht-vers.tech. Erträge / Aufwendungen
 7. Ergebnis der gewöhnlichen Geschäftstätigkeit
 8.-10. Außerordentliches Ergebnis
11. Steuern vom Einkommen und vom Ertrag
12. Jahresüberschuss/-fehlbetrag
13.-17. Rücklagenbewegungen → Bilanzgewinn
```

### Sparten-Trennung

Composite insurance is **largely barred** (§ 8 Abs. 4 VAG): Lebensversicherung-Konzession is mutually exclusive with andere Versicherungsklassen; same applies to substitutive Krankenversicherung. Large groups (Vienna Insurance Group, UNIQA, Generali) therefore run Leben/Sach/Kranken in **separate AGs** under one holding (Wiener Städtische AG, UNIQA Österreich AG, …). Where a single AG mixes Sparten in residual cases, § 146 requires a **separate vt. Rechnung per Bilanzabteilung** that only consolidates from item 7 (Ergebnis d. gewöhnlichen Geschäftstätigkeit) onwards.

### Size classes

**None apply.** § 189 UGB excludes Versicherungsunternehmen from the klein/mittel/groß ladder, so every concessioned VU files the full § 144 / § 146 schema. The only carve-out is the **kleiner Versicherungsverein** (§ 68 VAG, Beitragsvolumen roughly under EUR 5 M, no compulsory branches), which gets erleichterte Rechnungslegungs- und Solvenzvorschriften per §§ 70 ff. and § 79 VAG. For all stock companies (AG) and large VVaG: full schema, no abridged option.

## 3. Filing format in the Firmenbuch + Solvency II disclosures

### Firmenbuch filing

**Insurers (and banks) are currently exempt from the structured JAb 4.0 XML requirement.** The ERV 2021 Verfahrensbeschreibung includes an explicit carve-out: where structured submission is technically not possible *("dies betrifft derzeit insbesondere Banken und Versicherungen")*, the Jahresabschluss may be filed as **PDF**. So today an Austrian insurer's Firmenbuch filing is a **PDF of the audited VAG-Jahresabschluss**, not a machine-readable XML. There is **no VAG-specific XSD in JAb 4.0**; the JAb 4.0 taxonomy targets § 221 UGB companies only ([WKO Verfahrensbeschreibung](https://www.wko.at/oe/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/uebermittlung-jahresabschluesse-verfahrensbeschreibung.pdf), [WKO 2026 changeover](https://www.wko.at/information-consulting/unternehmensberatung-buchhaltung-informationstechnologie/buchhaltung/uebermittlung-der-bilanzen-an-das-firmenbuch-finanzonline)).

The BMJ JAb 4.0 XSD bundle is referenced from the [BMJ JAb 4.0 Änderungszusammenfassung](https://www.bmj.gv.at/dam/jcr:1d3d13d6-fba4-4ddd-8383-22b3669ca1d1/Zusammenfassung%20der%20%C3%84nderungen%20JAb%204.0.pdf) but is distributed through the gated ERV / JustizOnline integration portal; there is no stable public download URL. Verify with BMJ directly when needed.

### SFCR (Solvency II, Article 51)

Every EU/EEA insurer must publish the *Bericht über Solvabilität und Finanzlage* (SFCR) annually on its own corporate website, and file it with the FMA, which feeds it to EIOPA ([FMA Solvency II](https://www.fma.gv.at/versicherungen/solvency-ii/)). Format: **PDF narrative report with embedded standardised QRT tables**, structure fixed across Sections A-E (Business, Governance, Risk Profile, Valuation, Capital Management) under Implementing Regulation [(EU) 2023/895](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32023R0895).

**EIOPA does not run a free per-insurer SFCR-PDF library.** Aggregators such as [solvencyDATA.com](https://www.solvencydata.com/sfcr-solo-%C3%B6sterreich) maintain link directories (currently ~34 AT solo entities) and sell parsed datasets commercially.

### Public QRTs

The annual public QRT subset (Annex I of IR 2023/895, instructions in Annex II): **S.02.01.02** (Bilanz), **S.05.01.02 / S.05.02.01** (Prämien/Schäden je LoB und Land), **S.12.01.02** (Life TP), **S.17.01.02** (Non-life TP), **S.19.01.21** (Schadendreiecke non-life), **S.22.01.21** (LTG/Übergangsmaßnahmen), **S.23.01.01** (Own Funds), **S.25.01/02/03.21** (SCR), **S.28.01/02.01** (MCR). These are embedded as tables inside the SFCR PDF.

Taxonomy: [EIOPA Solvency II XBRL Taxonomy](https://www.eiopa.europa.eu/tools-and-data/supervisory-reporting-dpm-and-xbrl_en), current production v2.8.2. **Bulk per-insurer XBRL filings are confidential** between insurer-NCA-EIOPA and are not publicly downloadable. EIOPA does publish the [Insurance Statistics — Solo Annual](https://data.europa.eu/data/datasets/eiopa-insurance-statistics-solo-annual) open dataset, but it is largely country-aggregated with only some solo metrics. Granularity for AT should be verified before depending on it.

### Recommendation: Firmenbuch vs SFCR/QRT

|                       | Firmenbuch VAG-JA  | SFCR + public QRTs |
|-----------------------|--------------------|--------------------|
| Format                | PDF only (no XML)  | PDF narrative + standardised QRT tables; XBRL non-public |
| Coverage              | All AT insurers, ~9 months after FYE | ~40 AT solo + EU comparables, ~4 months after FYE |
| Machine-readability   | Low (PDF/OCR + bespoke VAG chart of accounts) | Medium (templates fixed across EU; parse by cell ID) |
| Comparability         | National only | EU-wide harmonised |
| Effort                | High and AT-only | High but pays back across 27 jurisdictions |

For an insurer financial-data product, **SFCR/QRT is the more useful primary source**: standardised templates, EU-wide comparability, capital + technical-provision granularity that no UGB Bilanz carries. The Firmenbuch insurer PDF mostly adds the German management report and is best used as a cross-check. Pragmatic path: scrape SFCR PDFs from each AT insurer's website (directory exists at solvencyDATA), extract the ~13 public QRTs via PDF-table parsing keyed to fixed cell IDs from IR 2023/895 Annex I, and treat the Firmenbuch insurer PDF as a secondary/optional source until BMJ extends JAb to VAG-Unternehmen (not on any published roadmap).

## 4. Ratios that make sense for insurers

### Why standard UGB ratios fail

Insurers have no industrial-style EBIT. Operating performance is the sum of the *versicherungstechnisches Ergebnis* (premiums earned − claims − operating expenses − Δ technical reserves) and the *Kapitalanlageergebnis*; EBIT collapses these heterogeneous flows. Current / quick / cash-conversion-cycle ratios are meaningless because there is no Umlaufvermögen vs Anlagevermögen split – assets are Kapitalanlagen duration-matched to versicherungstechnische Rückstellungen (ALM, not working capital). Eigenkapitalquote still computes but is structurally low (≈ 5-15 % of total assets) because reserves dominate liabilities; benchmarks come from the Solvency II SCR coverage, not from equity/total-assets ([Gabler Versicherungslexikon](https://www.versicherungsmagazin.de/lexikon/eigenkapitalquote-1985717.html), [Wikipedia Eigenmittel Versicherung](https://de.wikipedia.org/wiki/Eigenmittel_(Versicherung))).

### Insurer-specific ratios

P&C (Schaden/Unfall) – all computable from the Firmenbuch JA:

```
Schadenquote (Loss Ratio)            = Aufwendungen für Versicherungsfälle (f.e.R.)
                                        / Verdiente Prämien (f.e.R.)
Kostenquote (Expense Ratio)          = Aufwendungen für den Versicherungsbetrieb (f.e.R.)
                                        / Verdiente Prämien (f.e.R.)
Combined Ratio (Schaden-Kosten-Quote) = Schadenquote + Kostenquote
```

`f.e.R.` means *für eigene Rechnung* (net of reinsurance); a gross variant uses brutto positions. Denominator is canonically *verdiente* (earned) premiums; *verrechnete* (written) is acceptable as a simpler proxy ([Wikipedia Schaden-Kosten-Quote](https://de.wikipedia.org/wiki/Schaden-Kosten-Quote), [AK Wien Begriffe Jahresabschluss Versicherungen](https://wien.arbeiterkammer.at/service/betriebsrat/ifam/aufsichtsrat_in_banken/Begriffe_im_Jahresabschluss_von_Versicherungen.html)).

Additional ratios from the Firmenbuch JA:

```
Schadenrückstellungsquote     = Rst. f. noch nicht abgewickelte VS-Fälle / Verrechnete Prämien
Prämienwachstum (yoy)         = (Verrechnete Prämien_t − Verrechnete Prämien_{t-1}) / Verrechnete Prämien_{t-1}
Kapitalanlagenrendite         = Kapitalanlageergebnis / Ø Kapitalanlagen
RoE (Eigenkapitalrentabilität) = Jahresüberschuss / Ø Eigenkapital
```

Life / health additions:

```
Stornoquote                = Storno-Abgänge (Stück oder Vers.summe) / Ø Bestand
New-Business Margin (NBM)  = VNB / PVNBP   -- nur in Embedded Value / MCEV
```

Solvency II – **from SFCR, not Firmenbuch**:

```
SCR Coverage Ratio = Eligible Own Funds / SCR
MCR Coverage Ratio = Eligible Own Funds / MCR
```

### Source availability cheat-sheet

| Ratio | Firmenbuch JA | SFCR / QRT |
|---|---|---|
| Combined / Loss / Expense Ratio | yes | – |
| RoE, Premium growth, Kapitalanlagenrendite | yes | – |
| Schadenrückstellungsquote | yes | – |
| SCR/MCR Coverage, Eligible Own Funds, BEL, Risk Margin | – | yes |
| VNB / PVNBP / NBM | – | partly (EV/MCEV) |
| Storno-Quote | partial (Lagebericht) | yes (richer in SFCR) |

### Benchmarks

- **Combined Ratio**: <100 % = profitable underwriting, <95 % = strong; long-run DACH P&C ≈ 90-103 % ([Wikipedia](https://de.wikipedia.org/wiki/Schaden-Kosten-Quote)).
- **SCR Coverage (Austria)**: FMA minimum 100 %; Austrian median ≈ 235 %, above EU median ≈ 214 %. All AT insurers ≥ 100 %; the bulk are well over 200 % ([FMA solvency report](https://www.fma.gv.at/en/solvency-and-financial-condition-of-austrian-insurance-companies-improved-risk-absorbing-ability-high-quality-of-own-funds/)).
- **DACH life SCR ratios** typically 200-530 %; P&C 270-280 %; "ideal" band 125-350 % ([GDV Solvenzquoten](https://www.gdv.de/gdv/medien/medieninformationen/gdv-berechnungen-zu-solvenzquoten-deutsche-versicherer-sehr-stabil--132140)).

## 5. Detection heuristics

The Firmenbuch master record alone does not carry an "insurer" flag. Combine three independent signals and treat the entity as an insurer when at least two fire.

### Legal-form (Rechtsform) signals

- **AG (Aktiengesellschaft)** – dominant form for insurers (Vers.-AG) but *not* a unique flag; most AGs are not insurers. Combine with name / NACE / FMA join.
- **SE** – very few Austrian SEs total; not a unique flag.
- **VVaG (Versicherungsverein auf Gegenseitigkeit)** – **uniquely an insurer**. The name must contain the literal phrase "Versicherungsverein auf Gegenseitigkeit" (or the older "Wechselseitige Versicherungsanstalt"), regulated under §§ 64 ff. VAG.
- **Kleiner VVaG** – sub-category, partly exempt under § 68 VAG; same name pattern.

### Name patterns (case-insensitive)

Positive:

```
\b(Versicherung(s|en)?|Versicherungsverein|Wechselseitige[r]? Versicherung|
   Versicherungsanstalt|Assekuranz|Rückversicherung|Lebensversicherung|
   Sachversicherung|Krankenversicherung|Insurance|Reinsurance)\b
| \b(VVaG|V\.a\.G\.|Vers\.-AG|Re\s+AG|Re\s+SE)\b
```

False-positive exclusion (intermediaries, NOT insurers):

```
\b(Versicherungsmakler|Maklerei|Versicherungsagent(ur)?|Versicherungsberater|
   Versicherungsvermittl(er|ung)|Vermittlungs|-service|-beratung|Agentur|
   Treuhand|Anlageberatung)\b
```

### ÖNACE 2008 codes ([Statistik Austria FSK 65](https://fsk.statistik.at/wirtschaftszweige/oenace/65))

VAG-regulated insurer ⟺ ÖNACE ∈ {65.11 Lebensversicherungen, 65.12 Nichtlebensversicherungen, 65.20 Rückversicherungen}. **Exclude 65.30** (Pensionskassen – separate Pensionskassengesetz regime). **Exclude 66.22** (Versicherungsmakler und -agenturen – intermediaries). ÖNACE is not in the Firmenbuch record itself; you need to join WKO or Statistik Austria data to use this signal.

### FMA Konzessionsregister (authoritative)

The FMA *Unternehmensdatenbank* at <https://www.fma.gv.at/unternehmensdatenbank-suche/> supports a filter by sector `Versicherungsunternehmen`. **HTML only, paginated, no documented CSV/JSON/API**; Firmenbuchnummer not confirmed in listing fields. Best machine-readable substitute: the [ECB List of Insurance Corporations](https://www.bde.es/webbe/en/estadisticas/otras-clasificaciones/clasificacion-entidades/listas-instituciones-financieras/listas-empresas-seguros-pais/lista-ic-at.html) mirrored by Banco de España – downloadable as CSV, roughly 130 AT entries, with **LEI** plus name, address, type (life/non-life/composite/reinsurance). No FBNr directly, but the LEI → GLEIF lookup carries the AT FBNr in `RegistrationAuthorityEntityID` for AT-registered LEIs.

### Approximate population

- **FMA-supervised insurers in Austria**: ~74 (uncertain – FMA does not publish a single headline number).
- **VVO members 2025**: 114 total, of which 93 ordentliche (78 with HQ in Austria, 15 AT branches of foreign insurers) plus 21 außerordentliche ([VVO Jahresbericht 2025](https://vvo-newsroom.at/vvo-jahresbericht-2025-ist-online/)).
- **ECB / BdE list**: ~130 AT entries (includes branches and smaller entities).

Working estimate: **~75-95 VAG-licensed insurers with seat in Austria**.

### Reinsurance vs primary

Very few pure Austrian reinsurers. VIG Re is domiciled in Prague, not Vienna; its Austrian footprint is a branch. Austria has effectively 0-2 pure domestic reinsurers (some captive Re-vehicles possible); most "reinsurance business" runs as a side-licence of primary insurers. The ÖNACE 65.20 filter will catch what little exists.

## 6. Other reference material

- **FMA Versicherungen Abfragen** – [fma.gv.at/versicherungen/abfragen/](https://www.fma.gv.at/versicherungen/abfragen/)
- **FMA Unternehmensdatenbank** – [fma.gv.at/unternehmensdatenbank-suche/](https://www.fma.gv.at/unternehmensdatenbank-suche/)
- **Banco de España AT insurance corporations list (CSV with LEI)** – [bde.es … lista-ic-at](https://www.bde.es/webbe/en/estadisticas/otras-clasificaciones/clasificacion-entidades/listas-instituciones-financieras/listas-empresas-seguros-pais/lista-ic-at.html)
- **EIOPA Insurance Statistics – Solo Annual** – [data.europa.eu](https://data.europa.eu/data/datasets/eiopa-insurance-statistics-solo-annual)
- **EIOPA Solvency II XBRL Taxonomy hub** – [eiopa.europa.eu/tools-and-data/supervisory-reporting-dpm-and-xbrl](https://www.eiopa.europa.eu/tools-and-data/supervisory-reporting-dpm-and-xbrl_en)
- **VVO (Verband der Versicherungsunternehmen Österreichs)** – [vvo.at](https://www.vvo.at/) – annual statistics, member list, market data
- **VVO Jahresbericht 2024 Datenteil (PDF)** – [vvonet.vvo.at/…/VVO_Jahresbericht_2024_Datenteil.pdf](https://vvonet.vvo.at/vvo/vvonet_website.nsf/sysPages/Jahresbericht_2024_Daten.html/$file/VVO_Jahresbericht_2024_Datenteil.pdf)
- **VU-RLV (FMA Rechnungslegungsverordnung)** – [RIS BGBl. II 316/2015](https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=20009320)
- **Solvency II Wire** (commercial news/data, paywalled) – useful as a benchmark dataset but not free.

## Uncertainty flags

- The exact section range "§§ 136 to ~167" is verified through §§ 136-149; the higher sections (Bewertung, Anhang, Lagebericht, Offenlegung) were not extracted verbatim – verify exact numbering before depending on it.
- The VU-RLV Formblätter list (Abschnitt 6) was not retrieved at form-ID level; FMA download endpoint blocked.
- Confirm before relying: that the BMJ JAb 4.0 XSDs really contain *no* VAG-specific positions, and that the insurer PDF carve-out has not been rescinded in the latest ERV release.
- The ~74 FMA-supervised number is from a web summary, not an FMA-published headline – use the FMA database scrape as ground truth.
- EIOPA Solo Annual dataset granularity for AT solo entities should be confirmed by inspecting the CSV directly.
