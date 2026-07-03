# `tests/` — fixtures & cross-package integration

**Purpose:** shared **golden fixtures** (real Firmenbuch data) and, later,
cross-package integration tests. Per-package unit tests live next to their code in
`packages/*/tests/`; this directory holds the data they load and end-to-end checks
that span stages.

## Fixtures (`tests/fixtures/`)
| Path | What it is | Used by |
|---|---|---|
| `raw/030435h_2020-03-31_jb.xml` | legacy-format single filing (Bilanz only, `XXX_*` code, employees) | `parse` |
| `raw/030636d_2023-05-31_jb.xml` | legacy-format single filing (generator-comment personal data) | `parse` |
| `raw/490875a_multiyear/*.xml` | one company, 7 fiscal years, `PERS_KENN` siblings | `parse`, `consolidate` |
| `consolidated_examples/*.json` | expected-shape consolidated outputs from the prototype (number cross-checks) | `consolidate`, `derive` |

All three raw XMLs are the `legacy_finanzonline` variant (namespace
`finanzonline.bmf.gv.at/bilanz`, `POSTENZEILE/BETRAG`, `GJ`). **Still to add when
available** (not blocking, §16): a semantic **JAb 4.0** XML and a raw XML that
**contains a GuV**.

## Run
```bash
uv run pytest                 # whole workspace (per-package + integration)
uv run pytest packages/core   # one package
```

---
↑ [Repo root](../README.md) · Stages: [`core`](../packages/core/README.md) · [`parse`](../packages/70_parse/README.md)
