# Roadmap & Status — agentic-firmenbuch

> Single source of truth for where the product stands and what's next.
> Last updated: 2026-06-28. Numbers are live from production Cosmos + Blob checkpoints.

---

## Wo wir stehen (Live-Fakten, 2026-06-28, direkt aus Cosmos/Blob verifiziert)

| | Stand |
|---|---|
| **Im MCP-Server ausgeliefert (Layer 10)** | **341.197 Firmen** |
| davon mit Finanzkennzahlen (≥1 Abschluss) | **204.917** |
| nur Stammdaten (keine Publikationspflicht / nie eingereicht) | 136.280 |
| Register gesamt / aktiv / prüfbar (aktiv + Stammdaten) | 642.586 / 349.255 / 340.883 |
| **Filing-Check erledigt** (Ingest-Checkpoint) | **340.407 von 340.883 = 99,86 %** |
| GmbH-Abdeckung (GES mit Abschluss) | 191.659 von 213.618 = **90 %** |
| Datenaktualität | **täglich** — Daily-Change-Feed-Job lief 28.06. 03:00 UTC |
| MCP-Server | live, OAuth + API-Key, **Usage-Metering aktiv** (pro Key) |
| Verzeichnisse | offizielles MCP-Registry ✅, mcp.so eingereicht, Glama gelistet |

**Kurz:** Der Filing-Check ist **praktisch vollständig** (99,86 % der prüfbaren Firmen).
Es gibt **keinen** großen versteckten Rückstand. Die 136k „nur Stammdaten" sind kein
unverarbeiteter Stau, sondern Firmen **ohne Jahresabschluss** — überwiegend Rechtsformen
ohne Publikationspflicht (EU/KG/OG/PST ≈ 111k). Die Abdeckung ist nahe am Maximum für
UGB-pflichtige Firmen.

> **Frühere Fehlangaben korrigiert (28.06.):** „437k nie geprüft / 32 %" war gegen den
> **falschen Nenner** gerechnet (die vollen 642k inkl. ~293k historischer/gelöschter
> Firmen + change-feed-Stubs, die wir korrekt nie filing-checken). Gemessen an den
> prüfbaren Firmen sind 99,86 % erledigt. Auch „170k GmbHs nie gefetcht" war falsch
> (191.659 GES haben Abschlüsse).

---

## Sind die Daten aktuell?

**Ja.** Der Daily-Change-Feed-Job läuft jeden Tag um 03:00 UTC; neue/geänderte Firmen
fließen täglich nach. Der **Backfill** (Vollabgleich) war seit 23.06. geparkt und ist am
28.06. wieder aktiviert (täglich 04:00/06:00 UTC), um die letzten **5.910 dead-letter-
Firmen** zu bergen (siehe P1) — danach idlet er als Dead-letter-Backstop.

---

## P1 — Ingest-Gap (✅ weitgehend erledigt, deployed 2026-06-28)

**Realitätscheck (28.06., live aus Cosmos + Blob-Checkpoints):** Es gab **keinen**
170k/437k-Rückstand. Der Ingest-Checkpoint zeigt **340.407 von 340.883 prüfbaren Firmen
filing-checked = 99,86 %**. Die 136k „nur Stammdaten" sind Firmen **ohne** Abschluss,
nicht unverarbeitete – überwiegend Rechtsformen ohne Publikationspflicht:

| Rechtsform | aktiv | mit Abschluss | publikationspflichtig? |
|---|---:|---:|---|
| **GES** (GmbH) | 213.618 | **191.659** (90 %) | **ja** |
| **AG** | 1.160 | 510 | **ja** |
| KG | 42.241 | 12.067 | nur GmbH&Co KG |
| OG | 22.446 | 444 | nein |
| EU (Einzelunt.) | 56.405 | 20 | nein |
| PST | 2.954 | 1 | nein |

→ Die Abdeckung ist **nahe am Maximum** für UGB-pflichtige Firmen. Es gibt nichts „nachzu-
crawlen". Der **einzige echt bergbare Rest = 5.910 dead-letter-Firmen**, die alle am selben
Bug scheiterten (Fehlertext durchgehend `urkunde failed … http 200`) — darunter ganz
normale Großfirmen wie **Microsoft Österreich, IBM Österreich, GoodMills, Reisswolf**,
deren Jahresabschluss-**XML** schlicht > 10 MB ist.

Zwei Code-Fixes (beide deployed 28.06., Image `firmenbuch-pipeline:p1-19bdb60`):

1. **Großdatei-Parse-Bug behoben (der eigentliche Hebel).** ✅ Ursache war **nicht** Netzwerk:
   ein großes Filing kommt base64-kodiert als **ein** XML-Textknoten über libxml2s ~10-MB-Limit;
   der Default-Parser warf „Text node too long" → fälschlich als wiederholbares „http 200"
   gemeldet → nach Retries dead-letter. Fix: `huge_tree=True` (+ XXE-Schutz bleibt) plus
   granulares Timeout (kurzer Connect-, großzügiger Read-Timeout) statt der flachen 20 s.
   Code: `soap_client._try_parse` / `__init__` / `orchestration.__main__`. **Re-Drive
   automatisch:** dead-letters sind nicht im `done`-Set, der nächste Backfill-Lauf holt sie
   mit funktionierendem Parser → **berge die 5.910**.
2. **Backfill priorisiert publikationspflichtige Formen.** ✅ `ingestable_active_fnrs(priority=…)`
   ordnet GES → AG → GEN/SE/SPA/VER → KG vor den nie-einreichenden Schwanz. Wirkt bei jedem
   künftigen Re-Grind / Reset; jetzt kosmetisch, weil der Check ohnehin durch ist. Override:
   `INGEST_PRIORITY_RECHTSFORMEN`.

**Betrieb:** Backfill-Jobs waren seit 23.06. geparkt (Cron = „31. Februar"); am 28.06.
auf das neue Image gerollt und auf **täglich 04:00/06:00 UTC** reaktiviert. Sie bergen die
5.910 in 1–2 Tagen und idlen dann als Dead-letter-Backstop. **Blocker:** keine. **Erwartete
Wirkung:** +~5.910 Firmen mit Finanzdaten (u. a. namhafte GmbHs), danach ist P1 zu.

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
