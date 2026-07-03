# `core_at` — Austria-specific domain (`fbl_core_at`)

**Layer** | un-numbered (shared within the AT product, not a single data layer)
**Reads** | `fbl_core` (lineage/meta + metric contracts, storage, config)
**Writes** | nothing directly — provides the models/mappings/classifiers the AT stages use

The source-**specific** counterpart to the shared, source-agnostic
[`fbl_core`](../../../../packages/core/README.md). Everything here encodes Austrian
Firmenbuch / UGB knowledge and therefore must **not** be promoted to `packages/`:

| Module | What it owns |
|---|---|
| `mapping/` | the 317-entry UGB position taxonomy (`canonical`, `legacy_map` HGB, `jab40_map`) |
| `models/` | Firmenbuch/UGB domain models: `ParsedFiling`/`Bilanz`/`GuV` (filing), `ConsolidatedCompany`/`DerivedCompany` (company), `CompanyCard`/`SearchFilters`/`PresentedCompany` (mcp) |
| `classification/` | ÖNACE 2008→2025 crosswalk + branch classifier (`industry`, `crosswalk`, `taxonomy`, `keyword`) |
| `directories.py`, `financial_institution.py`, `esvg.py` | OeNB/EIOPA financial-institution registers + ESVG helpers |
| `austria.py` | `bundesland_from_plz` |
| `formats.py` | Firmenbuch XML variant detection (legacy vs semantic JAb 4.0) |

## Dependency direction (hard rule)

`fbl_core_at` → `fbl_core` only. The shared package never imports back. This is what
keeps `packages/core` genuinely source-agnostic and reusable by a second product
(see the reuse table in Technische Spezifikation Appendix R).

## Run its tests

```bash
uv run pytest products/agentic-firmenbuch/packages/core_at
```

---
↑ [AT product](../../README.md) · [Repo root](../../../../README.md) · Shared: [`core`](../../../../packages/core/README.md)
