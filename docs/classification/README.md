# Branch / industry (ÖNACE) classification — spec, status, target design

How the free-text Firmenbuch **Geschäftszweig** becomes an official **ÖNACE 2025** industry code
(issues #14, #23). Code lives in [`fbl_core.classification`](../../packages/core/src/fbl_core/classification/);
official source tables in [`docs/reference/oenace/`](../reference/oenace/).

This file is the **authoritative spec** for the feature: what is live today (v1), the known
defect and its root-cause analysis, and the corrected target design (v2). The served field
shapes are mirrored in [`docs/FIELD_REFERENCE.md`](../FIELD_REFERENCE.md).

## Why an LLM (not a lookup)

The Geschäftszweig is **free text** with no structured code: 84.8% coverage (~289k/341k
companies), a long tail of ~142k distinct phrasings. There is no free authoritative ÖNACE
source per company. Statistik Austria does assign every Austrian business an official ÖNACE
code (the "Klassifikationsmitteilung"), but that assignment is not public and not part of the
free HVD data, so we approximate it. GISA carries no ÖNACE (verified, see
Fachliche Spezifikation). A deterministic keyword mapper reaches the **section** for ~76% of
companies; the LLM lifts coverage and accuracy down to the **group** level.

The LLM performs exactly **one** step: free text → code. Everything else (hierarchy levels,
labels, NACE, crosswalk) is a deterministic lookup against the official tables. That division
of labour is deliberate and stays in v2.

> **Status 2026-07-02:** v2 is SHIPPED — class-level crosswalk, `industry` serving contract,
> head lexicon (1,832 texts / 112k companies), audit gates, daily-delta classification.
> The class-level re-grind covered ~64% of unique texts before the API credit ran out
> (audit green, uploaded); resume with `caffeinate -dims uv run python .grind/grind2.py
> --phase all` after topping up, which also runs the name pass. Details: issue #34.

## v1 — what was live before v2 (and its defect)

```
Geschäftszweig
  → LLM classifies against the ÖNACE 2008 GROUP catalogue (3-digit)   ← defect 1
  → official 2008→2025 crosswalk at GROUP level                        ← defect 2
  → derive section/division/labels from the 2025 tree (deterministic)
  → serve
```

Grind: 2026-06, model `claude-sonnet-4-6`, catalogue-constrained prompt, 289k companies
classified, 277k with a served `oenace` block. Self-reported confidence was removed by owner
decision (LLM confidence numbers are not calibrated). No name-based fallback exists: companies
without a Geschäftszweig have **no** `branch.oenace` (see "Gaps" below).

Decisions that were validated by measurement and **remain valid**:

| decision | evidence |
|----------|----------|
| Catalogue in the prompt (all valid codes) beats a bare prompt | section 76%→84%, invalid codes 42→11→0 with validation |
| Classify in 2008, map to 2025, beats classifying directly in 2025 | section +5pp, division +4pp, 0 invalid codes |
| Few-shot ≈ zero-shot | the model already knows ÖNACE; structure matters more than examples |
| Bottom-up (finest level first, derive parents) beats top-down | see "Top-down vs bottom-up" below |

Measured quality (fresh, held-out 150 companies, two independent pipelines + adjudicator):
section ~92%, division ~89%, group ~74% strict / ~82% adjudicated. Remaining group errors are
mostly fine distinctions within the correct division (wholesale subgroups, construction
trades).

### Known defect (root-cause analysis, 2026-07)

**Symptom.** Companies whose Geschäftszweig literally reads "Unternehmensberatung" are served
as ÖNACE 2025 **73.3 Public-Relations-Beratung**. Quantified live: 13,321 companies carry
group 73.3; at least 8,324 of them have "Unternehmensberat…" in the free text and are
unambiguously wrong.

**Root cause: an information-loss chain, not a model-judgement error.**

1. **The LLM was asked at group level (3-digit).** ÖNACE 2008 group 70.2 is a *combined*
   group ("Public-Relations- **und** Unternehmensberatung"); the distinction lives one level
   down (70.21 PR vs 70.22 consulting). The question already discarded the information the
   next step needed.
2. **The group-level crosswalk is lossy on split groups.** ÖNACE 2025 splits 70.2 into
   70.2 (consulting) and 73.3 (PR). A group-level table must pick one target; it picks 73.3,
   so *every* 70.2 company lands in the PR bucket.
3. **No post-grind audit.** A distribution check would have flagged 13k "PR agencies"
   (implausible for Austria) immediately.

The same pattern threatens every 2008 group that 2025 *splits*; 70.2 is only the largest
instance. The correction issue tracks the full split-group audit.

## v2 — target design (approved direction)

Six principles; together they make this failure class impossible rather than merely unlikely.

- **P1 — Decide once, at the finest level any downstream step needs.** The LLM outputs an
  ÖNACE 2008 **class** (4-digit, e.g. 70.22). After the judgement step, every transformation
  must be unambiguous (one input, exactly one output). Enforced by a **build-time test** over
  the crosswalk: every code the model may emit must map to exactly one 2025 group; otherwise
  the question to the model is too coarse and the build fails.
- **P2 — Same text ⇒ same code, structurally.** Classify **normalised unique Geschäftszweig
  strings**, not companies. All companies sharing a text inherit its code. Consistency becomes
  construction, not hope; an invariant check (one text, more than one code ⇒ alarm) becomes
  possible.
- **P3 — Verified head lexicon.** The most frequent texts (a few hundred strings cover the
  majority of companies) are classified once, reviewed, and **frozen as a deterministic
  text→code table**. The LLM only handles the long tail. Frequent cases are provably right,
  not probably right.
- **P4 — Closed catalogue, validated output.** Every emitted code is checked against the
  official tree (`is_valid`). Already live in v1 (0 invalid codes).
- **P5 — Audit invariants before serving.** After every grind: distribution plausibility
  (niche group suddenly holding percent-level share ⇒ alarm), lexicon-vs-LLM contradiction
  check, hierarchy consistency.
- **P6 — Golden-set regression.** Every corrected error becomes a test case. A future grind or
  method change must pass the golden set before its output is served. Once fixed, never
  silently wrong again.

Pipeline:

```
Geschäftszweig free text
  → normalise (P2)
  → in verified lexicon? → code (deterministic, provable)             (P3)
  → else: LLM, catalogue-constrained, ÖNACE 2008 CLASS level          (P1)
  → validate against tree (P4)
  → deterministic: class-level crosswalk 2008→2025, derive levels + labels (P1)
  → audit invariants over the full corpus (P5)
  → golden-set regression green (P6)
  → serve
```

### Top-down vs bottom-up (decided: bottom-up)

Top-down (pick section, then division within it, then group) multiplies error probabilities
and makes level-1 errors unrecoverable: the correct candidates are never seen again. Our own
eval shows why bottom-up wins here: group-level answers are ~74–82% right, but the *derived*
section is ~92% right, i.e. group misses are mostly near-misses inside the correct branch.
A top-down cascade would need well above 92% at level 1 alone plus near-perfect conditional
stages just to tie, at 3× LLM cost, with a worse failure mode (whole branch wrong). The full
catalogue fits in one prompt, so nothing forces a cascade. A two-stage narrowing for the long
tail is a measurable option (golden set), not the default. Top-down would also **not** have
prevented the v1 defect: that was a granularity/mapping loss, orthogonal to direction.

### Final served `industry` block (v2 contract)

The block is renamed **`branch` → `industry`** (owner decision: "branch" is ambiguous between
English *branch office* and German *Branche*; `industry` is unambiguous and matches the
English structural field names of the API). One assigned fact (the 2008 class, from lexicon
or LLM); everything else is a deterministic lookup from the official tables. `oenace` and
`nace` are **structurally symmetric** blocks: same shape, same codes (ÖNACE 2025 is identical
to NACE Rev. 2.1 at section/division/group level), ÖNACE carries the official German and
English titles, NACE carries the official English titles.

```jsonc
"industry": {
  "geschaeftszweig": "Unternehmensberatung",        // original free text, never dropped
  "oenace": {                                        // Austrian national classification
    "section": "N",
    "section_label_de": "Erbringung von freiberuflichen, wissenschaftlichen und technischen Dienstleistungen",
    "section_label_en": "Professional, scientific and technical activities",
    "division": "70",
    "division_label_de": "Verwaltung und Führung von Unternehmen und Betrieben; Unternehmensberatung",
    "division_label_en": "Activities of head offices; management consultancy activities",
    "group": "70.2",
    "group_label_de": "Unternehmensberatung",
    "group_label_en": "Business and other management consultancy activities",
    "version": "OENACE_2025"
  },
  "nace": {                                          // EU classification, same codes by construction
    "section": "N",
    "section_label": "Professional, scientific and technical activities",
    "division": "70",
    "division_label": "Activities of head offices; management consultancy activities",
    "group": "70.2",
    "group_label": "Business and other management consultancy activities",
    "version": "NACE_REV_2.1"
  },
  "code_2008": "70.22",             // the assigned fact (ÖNACE 2008 CLASS, 4-digit)
  "source": "lexicon",              // "lexicon" (verified head table) | "llm" (long tail)
  "classified_from": "geschaeftszweig"
}
```

Notes:
- The `nace` block is **not** a second mapping or model: ÖNACE 2025 is identical to EU NACE
  Rev. 2.1 down to the 4-digit class (Austria only adds 5-digit subclasses). Codes are copied,
  labels are the official English titles (already in the repo tables). NACE labels exist in
  all EU languages, but the German ones are the ÖNACE titles, so the `nace` block serves
  English only; German lives in `oenace`.
- Companies **without** a Geschäftszweig keep `industry: null` (honest gap, ~15%). No
  name-guessing: a company name is not evidence. If a name heuristic is ever added it must be
  flagged `source: "name_heuristic"` and default-off.
- This is a **breaking change** (block rename + `label` → `group_label_*` + structured `nace`).
  It ships in ONE break together with the data correction, announced in `felder.html`.
- Public wording stays: "LLM-classified (few-shot), tested against a reference"; no provider
  or dataset names anywhere.

### Migration plan (tracked in the correction issue)

1. Deterministic split-group repair: audit **all** 2008 groups that 2025 splits; resolve the
   affected companies via Geschäftszweig text rules; patch Cosmos. Free, fixes the 8.3k+ now.
2. Schema upgrade to the v2 block (serve-time derivation from the tables; labels all levels).
3. Head-lexicon build: top unique texts by frequency → classify once at class level → owner
   spot-review → freeze as table in `fbl_core.classification`.
4. Long-tail re-grind at **class** level (only texts the lexicon does not cover).
5. Audit invariants + golden set wired into the grind script; the grind refuses to upload on
   red.
6. Daily delta integration: new/changed companies classify on ingest (lexicon first, LLM for
   unknown text). Cost: cents per day.

## Gaps / open

- ~15% of companies have no Geschäftszweig → no industry block by design (v1: `branch: null`,
  v2: `industry: null`); see above.
- The daily change-feed does **not** yet classify new companies (step 6); until it ships, the
  corpus slowly drifts stale.
- `search_companies` filters (`oenace_section/division/group`) keep working unchanged; group
  values become more accurate after the repair.
