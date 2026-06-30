# `fbl_core.classification` — industry/branch (ÖNACE) classification

Everything the branch/industry feature (issue #14) needs to turn the free-text Firmenbuch
**Geschäftszweig** into an official **ÖNACE** industry code. Parent package; the pieces fan out:

| module | responsibility |
|--------|----------------|
| [`taxonomy.py`](taxonomy.py) | load the official ÖNACE 2008 / 2025 trees — `load_oenace_tree(year)`; candidate lists, code validation, bilingual titles |
| [`crosswalk.py`](crosswalk.py) | official ÖNACE 2008 → 2025 correspondence — `map_group()` |
| [`keyword.py`](keyword.py) | deterministic keyword → section mapper (`classify_oenace`); the zero-cost serve-time fallback |
| [`data/oenace/`](data/oenace/) | the official Statistik Austria source tables (trees + crosswalk + NACE Rev.2.1) |

## The classifier pipeline (offline/batch — never in the request hot path)

```
Geschäftszweig (free text)
  → LLM classify against the 2008 catalogue   (the vintage the model knows best; 0 invalid codes)
  → crosswalk.map_group()  2008 → 2025          (231/272 groups identity, 41 re-coded)
  → ÖNACE 2025 code + EU NACE Rev.2.1 + confidence
```

The LLM call itself lives in the batch/derive layer, not here — `core` stays deterministic and
network-free (§3.2). This package only supplies the taxonomy, the crosswalk, and the keyword
fallback so the served `branch` block can be validated and labelled.

## Measured quality

Validated on a fresh, held-out 150-company set (two independent pipelines + adjudication, not
the noisy third-party labels): **section ~92% · division ~89% · group ~74–82%**. Classifying in
2008 and mapping to 2025 beats direct-2025 by ~4pp on section/division. Full method comparison
and decisions: [`docs/classification/`](../../../../../docs/classification/README.md).

See also the served field contract in `fbl_mcp_server` (`branch` block) and the deterministic
serve-time fallback that `keyword.py` powers today.
