# Roadmap & Status — agentic-firmenbuch

> Single source of truth for where the product stands and what's next.
> Last updated: 2026-06-28. Numbers are live from production Cosmos.

---

## Wo wir stehen (Live-Fakten, 2026-06-28)

| | Stand |
|---|---|
| **Im MCP-Server ausgeliefert (Layer 10)** | **341.196 Firmen** |
| davon mit Finanzkennzahlen (≥1 Abschluss) | 204.917 |
| Im Register insgesamt bekannt | 642.588 |
| Filing-Check gelaufen (Abschlussliste geholt) | 205.298 (32 %) |
| **Noch nie Filing-Check** | **437.290 (68 %)** |
| Datenaktualität | **täglich** — Registry gestern gesweept, Filing-Check lief heute 03:50 UTC |
| MCP-Server | live, OAuth + API-Key, **Usage-Metering aktiv** (pro Key) |
| Verzeichnisse | offizielles MCP-Registry ✅, mcp.so eingereicht, Glama gelistet |

**Kurz:** Die Kern-Pipeline läuft und ist aktuell (täglicher Lauf). Das Produkt
funktioniert. Die größte Lücke ist **Abdeckung**: erst ein Drittel der Firmen hat
überhaupt einen Filing-Check.

---

## Sind die Daten aktuell?

**Ja.** Der Filing-Check-Job lief heute um 03:50 UTC, die Registry-Sweep gestern.
Neue Abschlüsse fließen täglich nach. **Aber:** „aktuell" heißt nicht „vollständig" —
68 % der Firmen wurden noch nie auf Abschlüsse geprüft (siehe Ingest-Gap). Eine Firma,
die du heute abfragst, kann „keine Daten" liefern, obwohl sie Abschlüsse hat — einfach
weil ihr Filing-Check noch aussteht.

---

## P1 — Ingest-Gap schließen (höchste Priorität, kein neuer Datentyp)

**Wichtige Korrektur (2026-06-28, per Rechtsform geprüft):** Die „437k nie geprüft" sind
KEIN echter 437k-Rückstand. Der Großteil sind Rechtsformen **ohne Veröffentlichungs-
pflicht**, die nie einen Jahresabschluss einreichen — sie zu prüfen bringt (korrekt)
nichts:

| Rechtsform | nie geprüft | hat Abschlüsse | publikationspflichtig? |
|---|---:|---:|---|
| **EU** (Einzelunternehmer) | 84.728 | **20** | nein |
| **KG** | 82.012 | 12.087 | nur GmbH&Co KG |
| **OG** | 48.535 | 444 | nein |
| **PST** (Privatstiftung) | 4.249 | 1 | nein |
| **GES** (GmbH) | **191.237** | 191.909 | **ja** |
| **AG** | 3.055 | 511 | **ja** |

→ **~220k der „nie geprüft" sind EU/KG/OG/PST** — die reichen fast nie etwas ein, da ist
nichts zu holen. Der **echte adressierbare Gap = ~191k GmbHs + ~3k AGs**, die einreichen
*müssen*, aber noch nicht verarbeitet sind. **Das** ist das Ziel.

Zwei Teil-Probleme, beide generisch – **beide im Code erledigt (2026-06-28)**, jetzt
arbeiten die geplanten Läufe den Rückstand ab:

1. **Backfill priorisiert jetzt publikationspflichtige Formen.** ✅ Der Filing-Check
   (`backfill-ingest`) prüft GES → AG → GEN/SE/SPA/VER → KG **zuerst**, der nie einreichende
   Schwanz (EU/OG/OHG/KEG/PST) zuletzt. So fließt das Per-Lauf-Zeitbudget in die ~194k
   Firmen, die wirklich einreichen, statt es an ~220k EU/KG/OG zu verschwenden. Reihenfolge
   per `INGEST_PRIORITY_RECHTSFORMEN` überschreibbar. Code:
   `Registry.ingestable_active_fnrs(priority=…)` + `orchestrator.backfill-ingest`.
2. **Großdatei-Download-Bug behoben.** ✅ Ursache war **nicht** Netzwerk: eine Banken-/
   Versicherer-PDF kommt base64-kodiert als **ein** Textknoten über libxml2s ~10-MB-Limit;
   der Default-Parser warf „Text node too long" → das wurde fälschlich als wiederholbares
   „http 200" gemeldet und nach Retries dead-letter. Fix: `huge_tree=True` beim Parsen (+
   XXE-Schutz bleibt). Zusätzlich granulares Timeout (kurzer Connect-, großzügiger
   Read-Timeout) statt der flachen 20 s, die bei langsamem Time-to-first-byte die größten
   Filings killte. Code: `soap_client._try_parse` / `__init__` / `orchestration.__main__`.
   **Dead-letter-Re-Drive automatisch:** fehlgeschlagene FNRs landen nie im `done`-Set, der
   nächste Lauf nimmt sie also von selbst wieder auf – jetzt mit funktionierendem Download.

**Blocker:** keine. **Offen:** nur noch Durchsatz (die Läufe müssen die ~194k abarbeiten) –
kein Code mehr. **Wirkung:** das Produkt fühlt sich „vollständig" an. Größter Hebel.

---

## P2 — Banken & Versicherer (eigenes Bilanzschema BWG/VAG)

**Kann man hier schon starten? Nein.** Ehrliche Antwort, zwei Gründe:

### Sind alle Finanzfirmen in Layer 90? Nein.

- Die großen (Erste Group, UniCredit, UNIQA Insurance, Wiener Städtische) wurden
  **noch nie filing-checked** → **gar nichts in Layer 90**.
- Andere (RBI, VIG) haben die Abschlussliste, aber der **Download ist gescheitert**
  (Großdatei-Bug, P1.2) → teilweise/nichts in Layer 90.
- Praktisch **alle echten Banken/Versicherer** haben entweder gar keine Rohdaten oder
  nur unvollständige. Sie kommen erst rein, wenn P1 läuft.

### Selbst in Layer 90 — verarbeitbar? Nein.

Aus der 64-Datei-Analyse (`docs/Erweiterungen_Spezifikation.md` §2):
- **100 % PDF** (kein strukturiertes BWG/VAG-XML existiert).
- **71 % der PDFs sind gescannte Bilder** → OCR nötig.
- Anderes Bilanzschema (BWG §§43-58 / VAG §§136-167) → unsere UGB-Pipeline parst sie nicht.

### Reihenfolge (aus `Erweiterungen_Spezifikation.md`):
1. **`is_financial_institution`-Flag** (~3 Tage) — Banken/Versicherer markieren, damit der
   Agent keine UGB-Kennzahlen draufrechnet. **Wertvoll auch ohne Finanzdaten.** Erster Schritt.
2. ESEF/iXBRL-Parser (börsennotierte, ~12 Firmen) — saubere IFRS-Daten.
3. EBA Pillar-3 (Banken: CET1/NPL/LCR) + SFCR/QRT (Versicherer: Combined Ratio/SCR).
4. PDF-Extraktion (BWG/VAG) inkl. OCR für die 71 % gescannten.

**Blocker:** P1 zuerst (sonst sind die Firmen nicht da). Dann ist Schritt 1 schnell;
2-4 sind echte Wochen-Projekte.

---

## P3 — GISA-Anbindung (Gewerberegister) ← bald gewünscht

**Was ist GISA?** Das österreichische **Gewerbeinformationssystem** (BMAW). Sagt,
**welche Gewerbeberechtigungen** eine Firma hat (Baumeister, Handel, Gastgewerbe …) —
faktisch „**was die Firma tun darf**". Der **fehlende Tätigkeits-/Branchen-Datenpunkt**
(NACE haben wir nicht — Gewerbe ist das österreichische Äquivalent).

**Warum es perfekt passt:** Methode **`SearchPersonJur` sucht nach Firmenbuchnummer** →
Gewerbe lassen sich **direkt an unsere Firmen anhängen** (Join über FN).

**Technik** (aus den Doku-PDFs, Stand V2/2022):
- SOAP oder REST/XML: `https://www.gisa.gv.at/gisa-svc-public/GisaPublicV2.svc/xml`
- Methoden: `SearchPersonJur` (per FN/Name) → `GetGewerbeV2` (Detail-XML + amtssignierter
  PDF-Auszug) · `GetVKR` (ausländische Versicherungs-/Kreditvermittler) · Katalog-Methoden.
- Rate-Limit: GetGewerbeV2/GetVKR 200/min pro Key; Auszüge 10/min.
- **Blocker:** **API-Key per Bürgerkarte beantragen, jährlich verlängern**
  (https://www.gisa.gv.at/sst-Neuausstellung). **Muss der Inhaber machen.** Einziger
  echter GISA-Blocker.

**Plan, sobald Key da:**
- Neuer Adapter `gisa_client` (analog `firmenbuch_client`) + Container `40_gewerbe` (per FN).
- Im Consolidate per FN joinen → `gewerbe[]` im Datensatz.
- MCP: Feld `gewerbe` + Suchfilter (z. B. „alle Baumeister-GmbHs in Tirol"), Tool
  `get_gewerbe(fnr)`.
- **Wert:** sehr hoch — endlich eine Tätigkeits-/Branchensuche, die das Firmenbuch nicht bietet.

---

## P4 — Ediktsdatei-Anbindung (Insolvenzen & Gerichtsedikte) ← bald gewünscht

**Was ist das?** Die **Ediktsdatei der Justiz** (edikte.justiz.gv.at) — amtliche
Bekanntmachungen: **Insolvenzen, Konkurse, Sanierungsverfahren, Versteigerungen**. Der
**Risiko-/Bonitäts-Datenpunkt**: „Ist über diese Firma ein Insolvenzverfahren offen?"

**Status:** Noch nicht recherchiert. Vor dem Bau braucht es einen Recherche-Durchgang
(offene API/HVD? oder nur Web-Suche/Scraping? Format? Join über FN?). **To-do:** kurze
technische Recherche, dann Bau-Spec. **Wert:** sehr hoch für M&A/KYC/Vertrieb.

---

## P5 — Kleinere offene Punkte

- Großdatei-`urkunde`-Download härten (= P1.2).
- Geplanten 03:00-Delta-Lauf stabilisieren (läuft auf Retry, erste Ausführung flaky).
- Inaktive/gelöschte Firmen aufnehmen oder „nur aktiv" bewusst bestätigen.
- `betriebserfolg` unter eigenem Namen ausweisen (+ optional striktes EBIT).
- `__stats__`-Refresh automatisieren (materialisierte Sicht für `get_coverage`).
- `firmenbuch_2025`-Parse-Variante an Live-Daten bestätigen.

---

## Empfohlene Reihenfolge

1. **P1 Ingest-Gap** — größter Hebel, kein Blocker, sofort. (Durchsatz: Tage–Wochen.)
2. **P2 Schritt 1: FI-Flag** — schnell (~3 Tage), verhindert Blamage bei Banken.
3. **P3 GISA** — sobald der Bürgerkarte-Key da ist (→ jetzt beantragen anstoßen!).
4. **P4 Ediktsdatei** — erst Recherche, dann Bau.
5. P2 Schritt 2-4 (echte Bank/Versicherer-Finanzdaten) — größtes Projekt, später.
6. P5 Kleinkram nebenbei.

**Dein einziger Hand-Blocker:** den **GISA-API-Key per Bürgerkarte beantragen**
(https://www.gisa.gv.at/sst-Neuausstellung) — nur der Inhaber kann das. Alles andere baue ich.
