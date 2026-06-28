# docs/reference/oenb — official OeNB source material

Official, free (CC-BY) reference data from the **Oesterreichische Nationalbank** used for the
register-based `is_financial_institution` flag (ROADMAP P2 / issue #15). Reference only — the
parser lives in `fbl_core.directories`, the sector legend in `fbl_core.esvg`.

| File | Source | What it is |
|---|---|---|
| `Sektor_ESVG_SL_Schluessel_2026-06-28.xlsx` | OeNB Metadata-Interface ([mdi entity m506931dd…](https://www.oenb.at/mdi/entity/m506931dd-af87-43e7-b9da-1c3b3beca4b5)) | The **ESVG (ESA 2010) sector key** — `Schlüssel → Bezeichnung`. The OeNB MFI/NMFI lists carry this code in their `E-VGR` column (1220A = banks, 1280* = insurers, 1290 = pension funds, 1250B = Mitarbeitervorsorgekassen, …). Extracted verbatim into `fbl_core.esvg.ESVG_LABELS`. |

The institution lists themselves are pulled at runtime (not stored here), each archived
verbatim + dated under Blob `90-raw/_directories/` (lossless history):
- `https://www.oenb.at/docroot/downloads_observ/MFI.csv` (monthly, ~398 banks, carries `FB-Nr`)
- `https://www.oenb.at/docroot/downloads_observ/NMFI.csv` (monthly, ~47 non-MFI credit institutions)

Retrieved 2026-06-28. If OeNB revises the sector key, re-download and regenerate `esvg.py`.
