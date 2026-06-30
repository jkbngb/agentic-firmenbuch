# ÖNACE 2025 classification tree

Official Austrian industry classification (Statistik Austria), the candidate taxonomy for the
branch/industry feature (issue #14). Loaded by [`oenace_tree.py`](../oenace_tree.py).

| file | content |
|------|---------|
| `oenace2025_de.csv` | German titles, full 5-level tree (CTI export, UTF-8, `;`-delimited) |
| `oenace2025_en.csv` | English titles, same rows/codes |

Columns: `Ebene` (1 section … 5 national subclass), `EDV-Code` (compact key, `A0111`),
`Code` (display, `A 01.11`), `Titel`, `Kurztitel`.

Five levels: **22 sections** (A–V) → **87 divisions** → **287 groups** → **651 classes** →
**711 national subclasses** (the Austrian `-0` extension).

> **Version note:** ÖNACE 2025 (= NACE Rev.2.1) re-letters the sections vs ÖNACE 2008 — the old
> *Information & Communication* section was split into two (J + K), so every section from the
> old K onward shifts by one letter (e.g. real estate `68` moved from section **L** to **M**).
> Codes also changed in places. The legacy deterministic mapper still emits 2008 sections;
> aligning the served branch field to 2025 is a deliberate migration step.

Source: Statistik Austria, ÖNACE 2025. Reference data only — no runtime network access.
