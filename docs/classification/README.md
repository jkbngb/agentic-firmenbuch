# Branch / industry (ÖNACE) classification — approach & results

How the free-text Firmenbuch **Geschäftszweig** becomes an official **ÖNACE** industry code
(issue #14). Code lives in [`fbl_core.classification`](../../packages/core/src/fbl_core/classification/);
official source tables in [`docs/reference/oenace/`](../reference/oenace/).

## Why an LLM (not a lookup)

The Geschäftszweig is **free text** with no structured code — 84.8% coverage (~289k/341k
companies), a long tail of ~142k distinct phrasings. There is no free authoritative ÖNACE
source per company (GISA carries none; verified). A deterministic keyword mapper reaches the
**section** for ~76% of companies and ships today as the serve-time fallback; the LLM lifts
coverage to 100% and accuracy well beyond keywords, down to the **group** level.

## Architecture (decided by measurement)

```
Geschäftszweig
  → classify against the ÖNACE 2008 catalogue   (the vintage the LLM knows best)
  → official 2008→2025 crosswalk                 (231/272 groups identity, 41 re-coded)
  → serve ÖNACE 2025 (+ EU NACE Rev.2.1) + confidence
```

Key decisions, each backed by a run on reference companies:

| decision | evidence |
|----------|----------|
| **Catalogue in the prompt** (all valid groups) beats a bare prompt | section 76%→84%, invalid codes 42→11 |
| **Classify in 2008, map to 2025** beats classifying directly in 2025 | section +5pp, division +4pp, 0 invalid codes |
| **Model = Opus 4.8** | best measured quality; the owner prioritised quality |
| **Few-shot ≈ zero-shot** | the model already knows ÖNACE; examples add ~1pp — structure matters more than wording |
| **Group only with high confidence**; section + division always | group is genuinely ambiguous on hard cases (retail vs wholesale) |
| **Name as fallback** for the ~15% with no Geschäftszweig | ~69% section from the name alone, flagged low-confidence |

## Measured quality (fresh, held-out 150 companies)

Measured by two independent pipelines + an adjudicator — **not** against the third-party
reference labels, which an audit showed are themselves ~10–15% wrong/ambiguous on the hard
cases (so the earlier ~85%-vs-reference number *understated* true quality).

| level | quality |
|-------|---------|
| section | **~92%** |
| division | **~89%** |
| group | **~74% strict → ~82% adjudicated** |

Remaining group errors are fine distinctions within the correct division (wholesale subgroups,
construction trades) — genuinely ambiguous, handled by the confidence gate.

## Served contract (`branch` block)

`get_company_details` and every search card carry:

```
branch: {
  geschaeftszweig,                       # original free text, never dropped
  oenace: { section, division, group, label },   # ÖNACE 2025
  nace_rev21,                            # EU code (NACE Rev.2.1)
  source,                                # llm | keyword | name
  confidence                             # high | medium | low
}
```

Section + division are served whenever present; group is served only at high confidence.
