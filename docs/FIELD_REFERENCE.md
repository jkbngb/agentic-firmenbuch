# Datenfelder-Referenz · Agentic Firmenbuch

> *English: this is the canonical field dictionary for the served data. Field names and
> financial-position labels are German (they mirror the official Firmenbuch / UGB terms).*

Diese Seite beschreibt **alle Felder**, die der MCP-Server liefert — pro Werkzeug, mit
Typ, Bedeutung und der Regel, **wann ein Feld leer (`null`) ist**.

**Wichtig:** Die Suche (`search_companies`) liefert bewusst nur eine **kompakte
Übersichtskarte** je Treffer — *nicht* alle Daten. Das ist Absicht (schnell, sparsam).
Für den vollständigen Datensatz eines Unternehmens ruft der Agent gezielt
`get_company_details` (vollständiges Profil) oder `get_full_record` (alles, was wir halten)
auf. Es gibt also drei Stufen:

| Werkzeug | Liefert | Wofür |
|---|---|---|
| `search_companies` | 10-Feld-**Karte** je Treffer | Suchen, Ranken, Überblick |
| `get_company_details` | **vollständiges Profil** (alle Abschnitte unten) | Detailansicht eines Unternehmens |
| `get_full_record` | **Obermenge** des Profils (volle Taxonomie + Lineage) | maximaler Detailgrad |

Jede Antwort trägt zusätzlich einen Umschlag: `schema_version`, `data_version`,
`provenance` (Quelle/Stand) — und bei der Suche `total`, `page`, `page_size`.

---

## 1 · `search_companies` → Karte (`results[]`)

Kompakter Auszug. **Codes sind hier bereits als Labels ausgegeben** (z. B. `GmbH`,
`Oberösterreich`).

| Feld | Typ | Bedeutung | `null`, wenn … |
|---|---|---|---|
| `fnr` | string | Firmenbuchnummer (z. B. `078052h`) | nie |
| `name` | string | Firmenwortlaut | nie |
| `legal_form` | string | Rechtsform-Label (z. B. `GmbH`) | unbekannt |
| `bundesland` | string | Bundesland-Label (z. B. `Wien`) | unbekannt |
| `size_gkl` | string | Größenklasse `W`/`K`/`M`/`G` (siehe §5) | unbekannt |
| `bilanzsumme_latest` | number € | Bilanzsumme des jüngsten Abschlusses | kein Abschluss vorhanden |
| `equity_ratio_latest` | number 0–1 | Eigenkapitalquote (jüngstes Jahr) | nicht berechenbar |
| `revenue_latest` | number € | Umsatzerlöse (jüngstes Jahr) | **kein GuV** im jüngsten Abschluss |
| `growth_profile` | string | `shrinking`/`stable`/`growing`/`fast_growing` | < 2 vergleichbare Jahre |
| `has_guv_latest` | bool | hat der jüngste Abschluss eine GuV? | – (immer gesetzt) |

---

## 2 · `get_company_details` → vollständiges Profil

Das Unternehmen liegt unter `result`. **Hier stehen die Roh-Codes** (`legal_form: "GES"`,
`bundesland: "O"`) — die Code-Tabellen stehen in §5.

### `identity`
| Feld | Typ | Bedeutung |
|---|---|---|
| `fnr` | string | Firmenbuchnummer |
| `register_id` | string | `AT_<fnr>` |
| `name` | string | Firmenwortlaut |
| `legal_form` | string | Rechtsform-**Code** (`GES` = GmbH-Familie; siehe §5) |
| `status` | string | `active` / `historical` / `deleted` |
| `court` | string \| null | zuständiges Gericht (oft `null`) |

### `location`
`country` (`AUT`), `bundesland` (**Code**, §5), `city`, `postal_code`, `street` (oft `null`).

### `company`
| Feld | Typ | Bedeutung |
|---|---|---|
| `stammkapital` | number \| null | Stammkapital (oft `null`) |
| `first_filing_year` / `last_filing_year` | int | erstes / letztes verfügbares Abschlussjahr |
| `filing_years_available` | int | Anzahl vorhandener Abschlussjahre |
| `founded_year` / `founded_source` | int / string \| null | Gründungsjahr, sofern abgeleitet |
| `description` | string \| null | derzeit nicht befüllt (V1) |

### `size`
| Feld | Typ | Bedeutung |
|---|---|---|
| `gkl` | string | Größenklasse `W`/`K`/`M`/`G` (§5) |
| `bilanzsumme_band` | string | `small` … `very_large` |
| `peer_percentiles` | object | Perzentil je Kennzahl **innerhalb der eigenen Größenklasse** (z. B. `bilanzsumme: 99.3`) |

### `financials`
| Feld | Typ | Bedeutung |
|---|---|---|
| `latest_year` | int | jüngstes Abschlussjahr |
| `has_guv_latest` | bool | GuV im jüngsten Jahr vorhanden? |
| `revenue_basis` | string \| null | Herkunft des Umsatzwerts |
| `latest` | object | Kennwerte des jüngsten Jahres (Teilmenge von `bilanz`/`guv`) |
| `bilanz` | object | **Bilanzpositionen** je Position → Zeitreihe |
| `guv` | object | **GuV-Positionen** je Position → Zeitreihe (**leer `{}`, wenn kein GuV**) |

**Bilanz-Positionen:** `bilanzsumme`, `eigenkapital`, `verbindlichkeiten`,
`anlagevermoegen`, `umlaufvermoegen`, `sachanlagen`, `finanzanlagen`, `vorraete`,
`forderungen`, `cash`, `rueckstellungen`, `stammkapital`, `kapitalruecklagen`,
`gewinnruecklagen`, `bilanzgewinn_verlust`.

**GuV-Positionen** (nur bei `has_guv`): `umsatzerloese`, `materialaufwand`,
`personalaufwand`, `abschreibungen`, `ebit`, `ebitda`, `jahresueberschuss`.

> **EBIT/EBITDA – Definition:** Der UGB-Abschluss (§ 231 Abs 2, Gesamtkostenverfahren)
> weist weder EBIT noch EBITDA aus. `ebit` ist der ausgewiesene **Betriebserfolg**
> (operatives Ergebnis vor Finanzergebnis und Steuern), `ebitda` = Betriebserfolg +
> `abschreibungen`. Das entspricht **nicht** dem strengen EBIT (Ergebnis vor Zinsen
> und Steuern inkl. Finanzergebnis), sondern ist als vereinfachte Näherung zu verstehen;
> bei Gesellschaften mit wesentlichem Finanz-/Beteiligungsergebnis (z. B. Holdings)
> weichen die Werte ab.
> Alle übrigen Positionen werden 1:1 aus dem Abschluss übernommen.

> **Bilanz-only vs. mit GuV:** Kleine Gesellschaften reichen oft nur eine Bilanz ein.
> Dann ist `guv = {}`, `revenue_latest = null`, und margenbasierte Kennzahlen
> (EBIT-Marge etc.) bleiben ohne Wert. Filtern lässt sich darauf mit `has_guv` /
> `has_guv_latest`.

### `ratios`
13 Kennzahlen, je als Zeitreihen-Objekt:
`equity_ratio`, `debt_ratio`, `debt_to_equity`, `working_capital_ratio`,
`anlagedeckungsgrad_1`, `ebit_margin`, `ebitda_margin`, `net_margin`,
`personalkostenquote`, `materialaufwandsquote`, `roa`, `roe`, `capital_profile`.

Jedes Kennzahl-Objekt enthält u. a.: `latest`, `latest_year`, `history` (Jahr → Wert),
`avg_3y`/`avg_5y`, `min_5y`/`max_5y`, `volatility`, `trend`
(`rising`/`stable`/`falling`), sowie Wachstumsmaße (`growth_1y`, `growth_3y_cagr`,
`growth_5y_cagr` …). Margen-/Ertragskennzahlen sind ohne GuV leer.

### `growth`
`profile` (`shrinking`/`stable`/`growing`/`fast_growing`) und `method`. `null`, solange
weniger als 2 vergleichbare Jahre vorliegen.

### `employees`
`{ latest, latest_year, history }` — **oft `null`**, da Beschäftigtenzahlen im Firmenbuch
nur lückenhaft vorkommen.

### `filings[]`
je Abschluss: `stichtag`, `format` (z. B. `legacy_finanzonline`, `jab_4_0`), `parsed`,
`gkl`, `eingereicht`, `doc_key`, `document_url`, `pdf_doc_key` (Verweise teils `null`).

### `events[]`
Registerereignisse (Vollzüge), **abgeleitet aus dem täglichen Änderungs-Feed** ab **1. Juli 2026**.

Der amtliche HVD-Auszug auf unserer Stufe liefert kein historisches Vollzugs-Logbuch. Statt es
abzufragen, **leiten wir Ereignisse ab**: Bei jeder täglichen Delta-Verarbeitung werden die
Stammdaten einer geänderten Gesellschaft mit dem Stand der Vorverarbeitung verglichen; eine
Abweichung wird als typisiertes Ereignis erfasst. Die Historie beginnt bewusst am **2026-07-01**
(sauberes Startdatum) – ältere Änderungen werden nicht rückwirkend rekonstruiert.

| Feld | Typ | Bedeutung |
|---|---|---|
| `date` | string | Datum der Feststellung (Lauf-Datum, ISO) |
| `type` | string | `name_change`, `seat_change`, `legal_form_change`, `management_change`, `capital_change` |
| `description` | string \| null | Kurztext, z. B. `vormals: …` |
| `source` | string | `change_feed_delta` (abgeleitet) bzw. `auszug` (selten, direkter Vollzug) |

### `financial_institution`
Nur bei regulierten Finanzunternehmen vorhanden, sonst nicht im Profil. Quelle ist das **amtliche
Register** (OeNB-Bankenliste; Versicherer via EIOPA/GLEIF in Vorbereitung), per Firmenbuchnummer
verknüpft – kein Namens-Raten.

| Feld | Typ | Bedeutung |
|---|---|---|
| `kind` | string | `bank`, `insurer`, `pensionskasse`, `vorsorgekasse`, `fund`, `other_financial` |
| `source` | string | `register` (amtliche Liste, eindeutig) oder `heuristic` (Namens-/Rechtsform-Fallback) |
| `caveat` | string | Hinweis, dass Banken (BWG) / Versicherer (VAG) nach eigenem Schema bilanzieren und UGB-Kennzahlen daher fehlen/abweichen |

Auf der Such-Karte erscheint dazu das Flag `is_financial_institution` (bool).

### `management`
| Feld | Typ | Bedeutung |
|---|---|---|
| `n_signatories_latest` | int | Anzahl Zeichnungsberechtigter (jüngstes Jahr) |
| `signatories_stable_years` | int | Jahre konstanter Geschäftsführung |
| `primary_manager.age` | int | **aktuelles Alter** des primären GF (jahresbasiert) |
| `primary_manager.birth_year` | int | **Geburtsjahr** (nur Jahr) |
| `primary_manager.role_label` | string | Funktion (z. B. `GESCHÄFTSFÜHRER/IN (handelsrechtlich)`) |
| `primary_manager.vertretung` | string \| null | Vertretungsart (Einzel/Gemeinschaft) |

> **Datenschutz (DSGVO):** Personennamen werden **nicht** ausgeliefert. Verfügbar sind nur
> **Alter** und **Geburtsjahr** (Jahr, ohne Tag/Monat) sowie Funktion/Vertretung —
> ausreichend für Nachfolge-Screenings, ohne die Person zu identifizieren.

---

## 3 · `get_full_record` → Obermenge

Enthält **alles** aus dem Profil **plus**:

- `financials.positions` — vollständige **317-Positionen-Taxonomie** (jede UGB-Position),
- `financials.passthrough` — **unbekannte Quell-Codes** inkl. Historie (verlustfrei),
- `financials.completeness` — Qualitätsmaß (Positionsanzahl je Jahr),
- `financials.guv_years` — Liste der Jahre mit GuV,
- `management.signatories_history` — Zeichnungs­berechtigte je Jahr,
- `derivations` — `metrics_version` + Formel-Registry der Kennzahlen.

Auch hier gilt: **Namen bleiben ausgeblendet** (DSGVO).

---

## 4 · Filter, Sortierung, Seiten (`search_companies`-Argumente)

**Filter:** `name` (Teilstring), `legal_form` (`GmbH` …), `bundesland` (Klarname, z. B.
`Wien`), `size_gkl` (`W`/`K`/`M`/`G`), `bilanzsumme_min`/`_max`, `equity_ratio_min`/`_max`,
`revenue_min`/`_max`, `employees_min`/`_max`, `growth_profile`, `has_guv`, `has_guv_latest`,
`last_filing_year_min`, `gf_age_min` (primärer GF mindestens X Jahre — Nachfolge-Screen),
`status` (`active`/`inactive`/`all`).

**Sortierung:** `sort = { field, descending }` über u. a. `bilanzsumme`, `equity_ratio`,
`revenue`. **Seiten:** `page` (ab 1), `page_size` (Standard 25).

---

## 5 · Code-Tabellen

**Bundesland** (Code → Klarname): `B` Burgenland · `K` Kärnten · `N` Niederösterreich ·
`O` Oberösterreich · `S` Salzburg · `St` Steiermark · `T` Tirol · `V` Vorarlberg ·
`W` Wien.

**Rechtsform:** Der granulare Firmenbuch-Code steht im Profil (`legal_form`). Die
GmbH-Familie ist das Präfix `GE…` (`GES` ≈ 99,7 %); die Suchkarte labelt das als `GmbH`.

**Größenklasse `gkl`:** `W` Kleinst/Mikro · `K` Klein · `M` Mittel · `G` Groß.

---

## 6 · Stand & Gewähr

Jede Antwort trägt `provenance.data_version` und `built_at` (Erstellungszeit der
ausgelieferten Daten). Die Daten stammen aus dem **österreichischen Firmenbuch**
(BMJ – Justiz, EU High Value Dataset, CC BY 4.0), werden automatisiert verarbeitet und
**ohne Gewähr** auf Richtigkeit/Vollständigkeit/Aktualität bereitgestellt. Maßgeblich ist
stets der amtliche Firmenbuchauszug.
