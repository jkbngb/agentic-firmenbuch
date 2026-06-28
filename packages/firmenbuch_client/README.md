# `firmenbuch_client` (`fbl_firmenbuch_client`) — Stage 3 · HVD API adapter

**Purpose:** a typed, hardened adapter over the Firmenbuch **HVD SOAP API** behind a
stable [`RegisterSource`](src/fbl_firmenbuch_client/source.py) interface (§8.2), so the
rest of the pipeline depends on the capability, not the SOAP details. Read-only.

## Confirmed against the live API
The behaviour here was **live-probed before building** — see
[docs/API_PROBE_FINDINGS.md](../../docs/API_PROBE_FINDINGS.md). Headlines:
- **Auth = `X-API-KEY` HTTP header** (not WS-Security).
- **`auszug` works** and returns rich master data (name, address, Geschäftszweig,
  persons with birth dates) — so master data is available pipeline-wide.
- **Change feeds work** → the `change_feed` delta branch is active.
- **`sucheFirma` caps at 1000** results (enumeration must prefix-walk).
- SOAP request element **order is schema-enforced**.

## The six calls (`RegisterSource`)
| Method | Endpoint | Returns |
|---|---|---|
| `suche_firma` | `sucheFirma` | `list[FirmaResult]` |
| `suche_urkunde` | `sucheUrkunde` | `list[UrkundeRef]` (XML + PDF docs) |
| `urkunde` | `urkunde` | `UrkundeContent` (base64-decoded bytes + detected format) |
| `auszug` | `auszug_v2` | `AuszugKurz` (master data; persons keep birth **year** only) |
| `veraenderungen_urkunden` | `veraenderungenUrkunden` | `list[DocChange]` |
| `veraenderungen_firma` | `veraenderungenFirma` | `list[FirmaChange]` (kind: Neueintragung/Änderung/Löschung) |

## Behaviour
- **Auth:** `X-API-KEY` header, `Content-Type: text/xml; charset=utf-8`.
- **Retry:** HTTP 429/5xx retried with exponential backoff; a **SOAP Fault** (served as
  HTTP 500) is deterministic and raises immediately. Exhausted retries / faults raise
  `FirmenbuchApiError` so ingest can dead-letter one company without failing the run.
- **FNR normalization:** responses vary (`030435h` / `30435 h` / `30435h`) →
  `normalize_fnr` yields the canonical zero-padded form.
- **Format detection** of downloaded documents uses the shared
  [`fbl_core.formats`](../core/src/fbl_core/formats.py) (one source of truth with `parse`).
- **GDPR:** `auszug` person birth dates are reduced to **year only** at this boundary (§8.7).

## Run it standalone
```bash
uv run pytest packages/firmenbuch_client     # offline VCR-style tests (no network)
```
Tests drive the client with `httpx.MockTransport` over **recorded** SOAP responses
(`tests/recorded/`, sanitized — synthetic person data); no live key needed.

## Definition of Done (§8.2) — met
VCR-style tests cover all six calls; format detection correct on the real sample XMLs;
429 backoff + fault paths verified; FNR normalization tested.
`ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
Adapter used by **`ingest`** (Stage 5) to fetch raw artifacts and by `sync-registry`.
Shared contracts/format-detection live in [`core`](../core/README.md); the downloaded
XML is parsed by [`parse`](../70_parse/README.md).

---
↑ [Repo root](../../README.md) · Specs: [Technische](../../docs/Technische_Spezifikation.md) · [Fachliche](../../docs/Fachliche_Spezifikation.md) · [API reference](../../docs/reference/JustizOnline_API_Complete_Reference.md) · [Probe findings](../../docs/API_PROBE_FINDINGS.md)
