# ÖNACE taxonomy + crosswalk data

Official Austrian industry classification (Statistik Austria) — the candidate taxonomy and
correspondence tables for the branch/industry classifier (issue #14). Loaded by
[`taxonomy.py`](../../taxonomy.py) and [`crosswalk.py`](../../crosswalk.py).

| file | content |
|------|---------|
| `oenace2025_de.csv` / `_en.csv` | ÖNACE 2025 (= NACE Rev.2.1) full 5-level tree, DE/EN titles |
| `oenace2008_de.csv` / `_en.csv` | ÖNACE 2008 (= NACE Rev.2) full tree — the LLM's strongest vintage |
| `oenace2008_2025_crosswalk.json` | official 2008→2025 group + subclass correspondence (derived from the xlsx) |
| `OENACE2008_2025_Korrespondenz.xlsx` | the official Statistik Austria correspondence table (provenance/source) |
| `oenace2025_nace21_de.csv` | ÖNACE 2025 ↔ EU NACE Rev.2.1 correspondence (for serving the EU code) |

CTI columns: `Ebene` (1 section … 5 national subclass), `EDV-Code` (`A0111`), `Code`
(display `A 01.11`), `Titel`, `Kurztitel`. UTF-8, `;`-delimited.

Counts — **2025**: 22 sections / 87 divisions / 287 groups / 651 classes / 711 subclasses.
**2008**: 21 sections / 88 divisions / 272 groups (231 map 1:1 to 2025, 41 were re-coded).

> **Vintage note:** ÖNACE 2025 re-letters the sections vs 2008 — the old *Information &
> Communication* section split into two (J + K), so every section from the old K onward shifts
> by one letter (real estate `68` moved from section **L** in 2008 to **M** in 2025). The
> classifier therefore predicts in 2008 (its strongest vintage) and maps forward with the
> official crosswalk.

Source: Statistik Austria, ÖNACE 2008 / ÖNACE 2025. Reference data only — no runtime network.
