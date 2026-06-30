# ÖNACE — official source material

Authoritative Statistik Austria classification tables behind the branch/industry feature
(issue #14). The machine-readable copies that the code actually loads live with the package at
[`fbl_core/classification/data/oenace/`](../../../packages/core/src/fbl_core/classification/data/oenace/);
this folder documents provenance. Approach & results: [`docs/classification/`](../../classification/).

## Sources

| source | what it is |
|--------|-----------|
| **ÖNACE 2025 (CTI)**, DE + EN | current official classification (= NACE Rev.2.1 + Austrian national `-0` subclasses); 22 sections / 87 divisions / 287 groups / 651 classes / 711 subclasses |
| **ÖNACE 2008 (CTI)**, DE + EN | previous classification (= NACE Rev.2); 21 sections / 88 divisions / 272 groups — the vintage the LLM classifier predicts in |
| **ÖNACE 2008 → ÖNACE 2025 Korrespondenz** (xlsx) | official correspondence table; 231/272 groups map 1:1, 41 re-coded |
| **ÖNACE 2025 ↔ NACE Rev.2.1** (txt) | maps Austrian subclasses to the EU NACE code, for serving the EU-comparable code |

All are open reference data from Statistik Austria. Reference only — never accessed at runtime;
the classifier reads the bundled package copies. Original CTI files are Windows-1252; the
bundled copies are converted to UTF-8.
