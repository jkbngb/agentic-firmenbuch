# Ediktsdatei / Insolvenzdatei — research & integration spec (ROADMAP P4, issue #9)

> Research outcome for attaching Austrian **insolvency / court-edict** status to our companies
> as a risk/credit signal. Backs GitHub issue
> [#9](https://github.com/jkbngb/agentic-firmenbuch/issues/9) and ROADMAP P4. Source of truth
> for the facts below: the official **BRZ/BMJ "IWG-Schnittstelle zur Edikte-Applikation —
> Insolvenzdatei", v2021-02-09** (archived in
> [`docs/reference/ediktsdatei/`](../reference/ediktsdatei/)), plus live probing on 2026-06-28.

## TL;DR

- There **is** an official, structured **JSON REST API** for the Insolvenzdatei (insolvency
  register) — no scraping needed. It is the cleanest possible upstream: paginated lists, a
  date-filtered delta query, and an explicit **deletions** list. It maps almost 1:1 onto our
  existing watermark + drift sync pattern (§15a).
- **Blocker (like GISA):** access is **authenticated and paid** — username/password issued by
  the **BMJ / BRZ**. The owner must request credentials. This is the only hard blocker.
- **The hard engineering problem is the JOIN:** insolvency records carry **no
  Firmenbuchnummer** and historically **no clean legal-vs-natural-person flag**. The debtor
  name is split arbitrarily across "Vorname"/"Nachname". So linking an edict to one of our
  companies is **name + location fuzzy matching**, not a clean FN join. This is where the
  accuracy risk lives and where most of the build effort goes.

## Why it's worth it

The single most-requested missing datapoint for M&A / KYC / sales-qualification: *"is there an
open insolvency proceeding against this company?"* Combined with the financials we already
serve, it turns the product from "what the company looks like on paper" into "…and is it in
trouble right now". High value for the same audience that uses the financials.

## 1. Access (the blocker)

| | |
|---|---|
| Host | `https://iwg.justiz.gv.at/` (production), `https://iwg-schulung.justiz.gv.at/` (training, same creds) |
| Auth | HTTP Basic — **username + password issued by BMJ/BRZ**, **kostenpflichtig** (paid) |
| Transport | TLS 1.2; IBM **Domino Access Services** (Domino REST) returning JSON |
| Legal basis | IWG (Informationsweiterverwendungsgesetz) — a signed reuse agreement (`IWG-Vereinbarung Ediktsdatei`) with BMJ |

**Action for the owner:** request IWG credentials for the Insolvenzdatei from the BMJ/BRZ
(the same office that issues the public-data agreements). Until then this is parked, exactly
like the GISA Bürgerkarte key (issue #8). The live API returns a Domino login form to
anonymous callers — confirmed 2026-06-28.

> There is also a `data.gv.at` dataset entry for the Ediktsdatei, but the actionable,
> structured, delta-capable access is the IWG REST API above; the open-data entry points back
> to it.

## 2. API shape

Base: `https://iwg.justiz.gv.at/edikte/id/idiwg8.nsf/api/data/`

**Two collections (views):**
- **`All`** — every currently-published insolvency edict, complete (all fields).
- **`Deletions`** — edicts deleted since the API went live; only `AZKey` / `Aktenzeichen` +
  `Dat_Loeschung`. Used to prune our local shadow copy.

**Three GET request types:**
1. `GET /api/data/collections/` → list the available views (`All`, `Deletions`).
2. `GET /api/data/collections/name/All?ps=100&page=N` → paginated entries, sorted by `AZKey`.
   - The `Content-Range` response header gives the total (`items 0-99/89306` → ~894 pages at
     `ps=100` as of the doc's example).
   - Search: `?search=<tokens>&searchmaxdocs=N&sortcolumn=AZKey` (always pass `sortcolumn=AZKey`
     so multi-page paging stays stable across the load-balanced servers).
   - **Server limit: 5000 results per search** — narrow the criteria or page the date window.
3. `GET /api/data/documents/unid/<UNID>?multipart=false` → one full edict. `multipart=false`
   returns the `Bausteine` rich-text as `text/html`, which is far easier to parse.

**Search syntax** (URL-encode `[`=`%5B` `]`=`%5D` `=`=`%3D` `>`=`%3E` space=`%20` `"`=`%22`):
`[Feld]=Wert` · `[Feld] CONTAINS Wert` · `[Feld] IS PRESENT` · `NOT` · `OR` · `AND` ·
group with `(` `)`.

## 3. Sync model (maps onto our §15a pattern)

- **Initial load:** page `All` from `page=0` to the last page (`Content-Range` total ÷ `ps`).
- **Daily delta:** `All?search=([Dat_Akt_Bekanntmachung]>=DD.MM.YYYY)OR([Dat_Ori_Bekanntmachung]>=DD.MM.YYYY)`
  → new + changed edicts since the watermark. (Mind the 5000-result cap → if a day's window
  ever exceeds it, page the date range.)
- **Deletions:** `Deletions?search=[Dat_Loeschung]>=DD.MM.YYYY` → remove those `AZKey`s
  locally. The **only** stable link from a deletion back to an `All` record is `AZKey`
  (`@unid` is NOT stable for this — per the doc's FAQ §7.1).

This is the same shape as our Firmenbuch change-feed: a watermark date, an upsert delta, and a
drift/deletion reconcile. Reuse the watermark + drift-report machinery conceptually.

## 4. Record fields (the ones we care about)

From the official example document (`@form: "Edikt"`):

| Field | Meaning | Use |
|---|---|---|
| `Aktenzeichen` / `AZKey` | court case ref (e.g. `013 001 S 00002/17`) / its stable key | identity + dedup + deletion join |
| `@form` | `Edikt` or `Loeschung` | record type |
| `Schuldner_Akt_Type` | **`J`** = juristische Person, `N` = natürliche | **filter to companies** |
| `Schuldner_Akt_Name`, `Schuldner_Akt_Vorname` | debtor name (company name split arbitrarily across both) | **the join key (fuzzy)** |
| `SchuldnerSuchfeld` | server-side concatenation of Vor+Nachname | name search field |
| `Schuldner_Akt_Ort`, `Schuldner_Akt_PLZ`, `Schuldner_Akt_StrNr`, `Schuldner_Akt_Staat` | debtor address | **join disambiguation** |
| `Verfahrenskurztext` / `Verfahrenstext` | proceeding type (e.g. `SRV` / Schuldenregulierungsverfahren; Konkurs; Sanierung) | what kind of proceeding |
| `Gericht_Code`, `Bundesland_Code`, `Gerichtshofsprengel_Code`, `Jahr` | court / region / year | context, filtering |
| `Bausteine` | the published edict body (Masseverwalter, deadlines, …) as rich text | detail / display |
| `Dat_Ori_Bekanntmachung`, `Dat_Akt_Bekanntmachung`, `Dat_Alle_*` (arrays), `Dat_Loeschung` | first/last publication, all dates, deletion | timeline + delta filter |

Field infixes: `_Ori_` = value at first publication, `_Akt_` = value at latest publication
(absent ⇒ `_Ori_` is still current), `_Alle_` = JSON array of all values. Absent items emit no
key (so treat missing = null).

## 5. The join problem (the core risk)

> FAQ §7.3, verbatim sense: *"there is (for historical reasons) no dedicated field separating
> and identifying legal persons; company names are split across the Vorname/Nachname fields by
> the clerk's preference."*

So we **cannot** join insolvency → Firmenbuch on FN. The plan:

1. **Pre-filter** to `Schuldner_Akt_Type = "J"` (juristische Person) to drop the large tail of
   personal debt-regulation cases (SRV) we don't care about.
2. **Candidate match** the reconstructed debtor name (`SchuldnerSuchfeld` / `Name`+`Vorname`)
   against our Firmenbuch `identity.name`, **constrained by location** (`PLZ`/`Ort` vs our
   `location`), to keep it tractable and precise.
3. **Score + threshold:** normalised-name similarity + exact PLZ match ⇒ high confidence;
   record a `match_confidence` and the matched FN. Below threshold ⇒ leave unlinked (served as
   "unmatched insolvency edicts" pool, never force a wrong link).
4. **Never overstate:** a served insolvency flag must carry its confidence + the source
   `Aktenzeichen`, so a consumer can verify. A false "is insolvent" is worse than a miss.

This matching layer is the real work. GISA (issue #8) is the cleaner companion here: GISA's
`SearchPersonJur` **does** take a Firmenbuchnummer, so once GISA is in, it can corroborate
name↔FN links and raise match confidence.

## 6. Proposed build (when credentials arrive)

- **New adapter** `ediktsdatei_client` (analogous to `firmenbuch_client`): the 3 GET calls,
  Basic auth from Key Vault, paging, the date-delta + deletions queries, JSON → typed models.
- **New raw container** `90-raw` prefix `_edikte/` (verbatim JSON, §5.1 lossless) + a new
  layer container, e.g. **`40_insolvency`** (reserved `40` already exists in the LAYER_MAP),
  keyed by `AZKey`.
- **Matcher** (new stage): `Schuldner_Akt_Type=J` → name+PLZ candidate match vs `99_registry`
  / `10_presentation` → `{fnr, az_key, confidence}` links. Resumable, like the backfill.
- **Consolidate join:** attach `insolvency[]` (open/closed proceedings + dates + type +
  confidence) to the served company document.
- **MCP surface:** a field `insolvency` on the company record + a search filter
  (`has_open_insolvency=true`) + a tool `get_insolvency(fnr)`. Gate behind confidence.
- **Schedule:** initial load once, then a daily `edikte-delta` job (watermark) + the deletions
  reconcile — slot beside the existing daily change-feed job.

## 7. Effort & risks

- **Effort:** medium. The API + sync are easy (a few days). The **name-matching layer is the
  bulk** and needs a precision/recall pass on real data before serving.
- **Risk — false links:** the FN-less join can mis-attribute an edict to the wrong company.
  Mitigate with the location constraint, a confidence threshold, and serving the source
  `Aktenzeichen` for verification.
- **Risk — GDPR/personal data:** the `All` view includes **natural-person** debt-regulation
  cases (SRV) with names/birth dates. We must **filter to `Type=J`** and never ingest/serve the
  natural-person tail — it is out of scope and personal data. Aligns with our names policy
  (companies only, year-of-birth elsewhere).
- **Risk — 5000-result search cap:** page date windows so no single query exceeds it.
- **Blocker:** IWG credentials (owner action), same gating as GISA.

## 8. Recommendation

Build order once credentials exist: **adapter + sync (easy, verifiable) → matcher (the real
work, validate precision on live data) → consolidate join + MCP field/filter/tool**. Pair with
GISA (#8) to lift match confidence via the FN-keyed `SearchPersonJur`. Until BMJ/BRZ
credentials are issued, this stays parked — but it is now fully scoped and buildable.

---
↑ [docs index](../README.md) · Reference: [`docs/reference/ediktsdatei/`](../reference/ediktsdatei/) ·
Roadmap: [P4](../../ROADMAP.md) · Issue [#9](https://github.com/jkbngb/agentic-firmenbuch/issues/9)
