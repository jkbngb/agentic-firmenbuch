# Branchen-Klassifikation & externe Register-Quellen — nüchterne Analyse

Stand: 2026-06-29. Diese Datei hält fest, was wir über **Branchen-/ÖNACE-Klassifikation** und die
externen Register (GISA, EIOPA/GLEIF, WKO) **empirisch** herausgefunden haben — was geht, was
nicht, und der empfohlene Weg. Quelle der Befunde: Live-Abfragen + Primärquellen (nicht Vermutung).

Verwandt: Issue [#14](https://github.com/jkbngb/agentic-firmenbuch/issues/14) (Branchenfilter),
[#49 / P3 GISA](ROADMAP.md), [#17] (Versicherer, erledigt).

---

## 1 · Geschäftszweig im Firmenbuch — die einzige Branchen-Information, die wir frei haben

Live-Analyse über die ~341k servierten Firmen (`company.description`, aus `auszug` →
`master.description`):

| Fakt | Wert |
|---|---|
| Firmen mit Geschäftszweig | **289.295 (84,8%)** |
| Format | **Freitext**, Median 30 Zeichen, ~75% einzigartig |
| Strukturierter Code (ÖNACE)? | **0%** — kein Code, reiner Fließtext |

Beispiele: „Immobilienverwaltung", „Baustoffhandel und Handel mit Zement", „Beteiligung und
Geschäftsführung", „Helicopter Transporte". Starker Kopf-Cluster (Top-Phrasen wiederholen sich:
„Handel mit Waren aller Art", „Vermögensverwaltung", „Holding", „Gastgewerbe").

**Deterministischer Mapper gebaut** (`packages/core/src/fbl_core/oenace.py`, kein LLM, kein Netz):
kuratierte Keyword-Regeln → die 21 ÖNACE-2008-Sektionen, Output `(section, label, confidence)`,
`None` für den mehrdeutigen Schwanz. **Auf 8.000 Prod-Firmen gemessen: 76% klassifiziert** (62%
hoch + 14% mittel, multi-aktivität), 24% → LLM-Schwanz. Top-Sektionen: G/Handel, L/Immobilien,
K/Finanz-Holding, C/Herstellung, M/Beratung, F/Bau, I/Gastro, H/Transport.

→ **Sektions-Level ist machbar.** Hybrid: der Mapper deckt 76% gratis+deterministisch; der LLM-Tail
nur die restlichen 24%. Validität (echte Genauigkeit vs. wahre ÖNACE) braucht noch ein **Gold-Set**
(~300 handverifizierte Paare) zum Messen. Abteilungs-Level (88 Klassen) wäre rauschiger.

## 2 · GISA (Gewerbeinformationssystem) — KEIN ÖNACE. Fünffach verifiziert.

Annahme war „GISA hat ÖNACE pro Firma". **Falsch**, an 5 unabhängigen Live-Quellen widerlegt:

| Quelle (live geprüft) | ÖNACE enthalten? |
|---|---|
| OGD-Katalog `stat02` (Gewerbeschlüssel↔Wortlaute) | nein |
| OGD-Dump `stat03` (~1 Mio. Gewerbe-Datensätze) | nein |
| WSDL-Datenvertrag der API (350 Felder) | **kein nace/önace-Feld** |
| Offizielle 20-Seiten-API-Doku (BMWET) | 0 ÖNACE-Erwähnungen |
| **Live `GetGewerbeV2` (echter Key, echte GmbH)** | **NEIN** |

**Was GISA tatsächlich liefert** (Live-Response `GetGewerbeV2`): `GISAZahl`, `GewerbeSchluessel`
(GISAs **eigener** Zahlencode, z.B. `599999`), `Wortlaut` (Gewerbe-Freitext, z.B. „Handel mit
Elektro-Haushaltungsgeräten"), `GewerbeArt` (reglementiert/frei), `Behoerde`, `RechtswirksamAb`,
`IstIndustriebetrieb`, `Ruhend`, `Historie` — **kein ÖNACE**.

**Begriffsklärung (wichtig, wurde verwechselt):**
- `GewerbeSchluessel` ist **NICHT** die Firmenbuchnummer und **nicht** ÖNACE — es ist GISAs interner
  Gewerbe-Typ-Code. FN, GISA-Zahl und Gewerbeschlüssel sind **drei verschiedene IDs**.
- `Wortlaut` im Gewerbe-Datensatz = **Tätigkeitsbeschreibung** (Gewerbe), **nicht** der Firmenname.
- Den Firmennamen (Firmenwortlaut für juristische Personen) trägt der `SearchPersonJur`-Treffer; er
  stammt wie der Firmenbuch-Name aus dem Firmenbuch und sollte i.d.R. übereinstimmen (Formatierung/
  Historienstand kann abweichen).

**API-Struktur** (öffentliche GISA-Schnittstelle V2, SOAP/REST, `GisaPublicV2.svc`):
- `SearchPersonJur(input{Apikey, Firmenbuchnummer|Name})` → Person + GISA-Zahl(en). **Trägt die FN**
  als Such-Input → keyed Tier kann GISA↔Firmenbuch **per FN deterministisch** joinen.
- `GetGewerbeV2(input{Apikey, GISAZahl})` → der Gewerbe-Datensatz oben.
- `Apikey` ist ein **Request-Feld** (kein Header); Key via ID-Austria, 1 Key/Person, 1 Jahr gültig.
- Öffentlicher Endpoint: `https://www.gisa.gv.at/gisa-svc-public/GisaPublicV2.svc/{soap,xml}`
  (das WSDL nennt eine **interne** LB-Adresse `gisapub.lb.magwien.gv.at:7443`, die öffentlich nicht
  auflöst — Endpoint muss auf die `www.gisa.gv.at`-URL überschrieben werden).

**ToU / Recht:** Gratis, aber **Massenabfragen explizit verboten** (Schnittstellen-ToU Punkt 9 +
GewO §365e Abs. 3): jede Abfrage muss auf **eine einzelne** Person/Firma gerichtet sein; „Branchen-
oder Gründerlisten" sind untersagt. Die **Anzahl** ist egal — die **Methode** (Branchen-Sample) ist
verboten. Rate-Limit `GetGewerbeV2`/`GetVKR`: 200/min/Key; Zugriffe 7 Jahre geloggt.

**OGD-Dump** (data.gv.at, CC BY 4.0, Datei-Download = keine ToU-Schranke): liefert
`gewerbewortlaut → gewerbeschluessel` in Millionen Zeilen, aber **anonymisiert** (kein FN, kein
Name, kein ÖNACE). Spalten stat03: `nuts1..3,lau1,lau2,adress_art,gewerbeschluessel,gewerbewortlaut,
gewerbeart,plz,ortschaft,rechtswirksam,ruhend_von,inhaber_pers_art`.

**Fazit GISA:** für **ÖNACE unbrauchbar** (existiert dort nicht). Könnte allenfalls eine Branchen-
Näherung über `gewerbeschluessel` geben — aber andere Taxonomie als ÖNACE, und der einzige
joinbare Weg (keyed API per FN) verbietet Massenabfragen. **Kein Skalierungs-Hebel.**

## 3 · Versicherer-Quelle EIOPA + GLEIF — erledigt, live (Kontext)

Banken: OeNB MFI/NMFI (FN-keyed, stabil). Versicherer: EIOPA-Register (Identität + LEI, AT-Filter)
→ GLEIF `entity.registeredAs` gated auf `registeredAt.id==RA000017` = FN. 42 Versicherer live im
`00_directories`. Robustheit: Snapshot-Fallback + Sanity-Gate + Mass-Deactivation-Guard +
E-Mail-Alarm. Details: [[register-fi-flag]], Issue #15/#17. EIOPA hat keine stabile API (SharePoint-
WebForms-POST-Scrape).

## 4 · WKO `firmen.wko.at` — Machbarkeitsanalyse: NICHT brauchbar (Scraper-only, blockiert)

Ein WKO-Firmen-A-Z-Eintrag trägt *im Prinzip* genau das Gewünschte: **Firmenname + FN + ÖNACE-Code
+ UID + Gewerbe** (die Daten kommen aus GISA, FN ist eine Such-Facette). **Aber:**

| Frage | Befund |
|---|---|
| Offizielle API / OData / Open-Data / Bulk? | **Nein.** Der data.gv.at-„Firmen A-Z"-Eintrag ist eine **Application (Link-out)**, kein Datensatz. Keine Maschinenschnittstelle. |
| Einziger Weg | **HTML-Scraping** — und die Seite **503t jeden automatischen Client** (auch `/robots.txt`), Result-Caps (<200 Treffer), keine Scraping-Erlaubnis. |
| Fachgruppe→ÖNACE-Konkordanz publiziert? | **Nein** (nur die ÖNACE-Struktur selbst; kein zeilenweises Join-File). |
| FN-Join | Deterministisch *möglich* (FN ist Facette), aber nur **per Scraping erreichbar** → praktisch blockiert. |
| Abdeckung GmbHs | ~80-90% der **aktiv tätigen** GmbHs; systematische Lücken bei **Holdings, Freien Berufen, ruhenden** Gesellschaften — also genau dem Firmenbuch-typischen Schwanz. |

**Verdikt:** firmen.wko.at ist **keine** nicht-Scraper-Quelle. Man baut exakt den fragilen,
rechtlich exponierten Scraper, den wir vermeiden wollten — für **partielle** Abdeckung. Nicht
empfohlen. (Quellen: `firmen.wko.at/SearchHelp.aspx` (503), `data.gv.at/application/firmen-a-z/`,
`wko.at/zahlen-daten-fakten/oenace`.)

## 5 · Empfehlung (höchster Mehrwert)

1. **ÖNACE/Branche kommt NICHT aus GISA.** Der tragfähige Weg ist ein **LLM-Batch-Klassifikator**
   über den vorhandenen Freitext-Geschäftszweig (offline, kein Hot-Path → spec-konform), Output
   `(önace_sektion, konfidenz)`, nur Hochkonfidenz als Filter.
2. **Gold-Set zuerst:** ~300 eigene Geschäftszweig-Werte handlabeln, um Genauigkeit zu **messen** —
   ohne das ist „reliable" nicht belegbar.
3. **Hybrid:** deterministische Keyword-Regeln für den eindeutigen Kopf (~55%, gratis), LLM nur für
   den Schwanz.
4. WKO nur weiterverfolgen, falls §4 eine **nicht-Scraper-Quelle** mit FN-Join ergibt; sonst ist die
   Freitext-Klassifikation der bessere Hebel.
