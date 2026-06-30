# HVD API probe findings (resolves Technische Spezifikation §16)

**Date:** 2026-06-16 · **Base URL:** `https://justizonline.gv.at/jop/api/at.gv.justiz.fbw/ws`
· **Method:** a handful of live calls against the configured HVD key (read-only).

These were live-confirmed before building `firmenbuch_client` (Stage 3). They settle
the open items in §16 so the build follows the confirmed branch instead of guessing.

## Confirmed

| # | Question (§16) | Finding | Consequence |
|---|---|---|---|
| Auth | WS-Security or `X-API-KEY`? | **`X-API-KEY` HTTP header works** (HTTP 200). WS-Security UsernameToken is **not** needed. The AI-generated reference doc is wrong here; spec §8.2 is right. | Client sends `X-API-KEY` + `Content-Type: text/xml; charset=utf-8`. |
| 1 | Does `auszug` work on this tier? | **YES.** `auszug_v2` (ns `…/Abfrage/v2/AuszugRequest`, `UMFANG=Kurzinformation`) returns rich master data: current name, full address, Geschäftszweig (+ code), Sitz, Rechtsform, representation rules, **persons with birth dates**, court, registration history. | `consolidate` uses real master data (not just `sucheFirma`). Birth dates available from `auszug` too. **GDPR:** gate names + minimize per §8.7. |
| 1 | Do the change feeds work? | **YES.** `veraenderungenUrkunden` and `veraenderungenFirma` both return HTTP 200 with thousands of entries per day. | Active delta branch = **`DELTA_MODE=change_feed`** (not rolling rescan). |
| 1/17 | Is `sucheFirma` capped? | **YES — exactly 1000 results**, no native paging (broad `a*` GES → 1000). | Enumeration fallback must prefix-walk/partition with `EXAKTESUCHE=true` (§15a.1). |
| — | XML vs PDF availability | A sample company had **39 Jahresabschluss docs: 18 XML + 21 PDF** (code 48). Older years XML, recent often both. | `parse` prefers XML; store both; PDF-only → linked stub. |

## Request shapes (live-verified)

- Endpoints: `{base}/{sucheFirma|sucheUrkunde|urkunde|auszug_v2|veraenderungenUrkunden|veraenderungenFirma}`.
- Request namespace: `ns://firmenbuch.justiz.gv.at/Abfrage/<X>Request` (auszug uses `…/Abfrage/v2/AuszugRequest`).
- **Element order is schema-enforced.** `sucheFirma` requires `FIRMENWORTLAUT, EXAKTESUCHE,
  SUCHBEREICH, GERICHT, RECHTSFORM, RECHTSEIGENSCHAFT, ORTNR` in that order (omitting
  `GERICHT` triggers a `cvc-complex-type.2.4.a` validation fault, HTTP 500).
- `urkunde` returns the document base64 in `DOKUMENT/CONTENT` (+ `CONTENTTYPE`/`DATEIENDUNG`).
- `veraenderungenFirma` `ARTDERVERAENDERUNG` ∈ {`Neueintragung`, `Änderung`, `Löschung`, …};
  `Löschung` → status `deleted`, `Neueintragung` → new FNR.

## Data quirks to handle

- **FNR formatting varies:** `030435h` (canonical), `30435 h` (response `REQUEST_FNR`),
  `30435h` (inside the filing XML `FNR`). The client/ingest must **normalize FNR**
  (strip spaces; the leading zero is not always present in payloads).
- Responses declare *all* request/response namespaces on the root with `ns2…ns19`
  prefixes — parse by **local name**, never by prefix.
- A real downloaded filing (FNR 030435h, 2008-03-31) parsed cleanly with **negative
  equity** (EK −64130.11) and aktiva == passiva — validates Stage 2 on live data.

## data.gv.at HVD bulk dataset (§16 #2) — probed 2026-06-16

The HVD dataset exists and is published on data.gv.at
(`datasets/e91bd464-be86-453c-b693-2ab818e11df2`, announced 2025-03-17). It surfaces the
free BMJ/JustizOnline **API** (the same SOAP endpoints used here) plus company
information, documents, and financial statements.

**Finding:** I could **not** confirm a single downloadable **bulk file containing the
full FNR list**. The data.gv.at portal is a JS single-page app and its CKAN-style
package API (`/api/3/action/package_show`) returns 404; web search did not surface a
published "Komplettabzug"/full-FNR file. So:

- **Operational seed = the hardened `sucheFirma` prefix-walk** (depth 20, exhaustive
  split alphabet, checkpoint/resume, loud completeness self-check) — see `90_ingest`.
- **Bulk is wired as the PREFERRED seed via a pluggable hook** (`fbl_ingest.bulk.BulkSource`):
  the moment a bulk full-FNR file/URL is configured, `sync_registry(..., bulk=...)` uses
  it (it is the only true completeness guarantee) and the prefix-walk becomes the fallback.

> Re-confirm if BMJ later publishes a bulk full-FNR file (or an account with portal
> access can enumerate the dataset's resources); then point a `BulkSource` at it.

## Still open (non-API)

- **Bulk full-FNR file** (§16 #2): not confirmed available; prefix-walk is the
  operational seed, bulk hook ready (see above).
- **GDPR lawful basis** before flipping `EXPOSE_PERSONAL_DATA` (§16 #6) — policy, not code.
- **JAb 4.0 semantic sample** to finalize v4 leaf paths (§16 #3) — extractor exists;
  validate against a real semantic filing when one appears.

---
↑ [Repo root](../README.md) · [Technische Spezifikation §16](specs/Technische_Spezifikation.md) · [API reference](reference/JustizOnline_API_Complete_Reference.md)
