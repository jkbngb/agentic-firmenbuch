# How we mirror an entire national company register and keep it fresh

> Public architecture note. This explains the **logic** of how we build and maintain a complete,
> daily-fresh, lossless mirror of the Austrian company register (Firmenbuch) from the official
> High Value Dataset. It is intentionally an architecture write-up, not a runnable collector:
> the operational specifics of talking to the metered official API stay private. The goal here
> is to show that the data is built honestly and reproducibly — no scraping, no guesswork.

## The problem

A national company register is a moving target. In Austria, hundreds of thousands of companies
each carry master data (name, seat, legal form, management, business purpose) and file annual
financial statements (Jahresabschluss) as structured XML or, for many, only as PDF. The data is
an EU **High Value Dataset** and free to use (CC BY 4.0), but it is served through an official
API one company (and one filing) at a time. Turning that into a queryable, comparable dataset
means solving three problems at once:

1. **Completeness** — cover *every* company, not just the ones you happened to look up.
2. **Freshness** — reflect new filings, new companies, and deletions within a day.
3. **Losslessness** — never silently drop a field, a position, or a filing on the way in.

Everything below is a deterministic ETL. There is no LLM in the data path; the "agentic" part
sits on top, as an MCP server that answers questions over the finished dataset.

## 1. Completeness: enumerate the whole register, resumably

You cannot rely on look-ups to discover the universe — you have to enumerate it. We do a
**systematic sweep** of the register's search surface that walks the identifier space in small
prefixes until every company has been seen. The important part is not the walk itself but that
it is **resumable and self-correcting**:

- The sweep persists its frontier, its done-set, and its running counts to a single checkpoint
  blob periodically. If the job is killed or crashes mid-sweep, the next run **resumes** where it
  left off instead of starting over; a fully completed sweep clears the checkpoint so the next
  scheduled run walks fresh.
- The checkpoint stores only the *keys* of companies already seen (rebuilt as lightweight
  placeholders on load), never their payloads — those are already streamed into the registry as
  they are found. That keeps the checkpoint small and makes the "mark vanished companies as
  deleted" reconcile safe across a resume: it can never wrongly delete a company it simply hasn't
  re-reached yet.

The full sweep is expensive (a many-hour to multi-day grind against a rate-limited API), so it
runs on a **quarterly** cadence with generous headroom, as the completeness backstop — not the
steady state. The steady state is the daily delta below.

## 2. Freshness: a daily change-feed delta

Every day, a cheap delta run advances the dataset off the register's **change feed**:

- It reads a **watermark** (the last processed change date) from the registry, pulls everything
  that changed since, and moves the watermark forward.
- Each changed company is marked **dirty** so only the affected records flow through parsing,
  consolidation, and the derived/served projection — the rest of the pipeline does no work.
- Brand-new companies seen in the feed (Neueintragung) are inserted; deletions (Löschung) flip
  the company's status. Per-company state (known filings and their content hashes, pipeline
  dirty/clean, data version, last-checked timestamp) is updated in the same run.

The result: a new annual filing or a new company shows up in the served data within a day,
without re-touching the whole register.

## 3. Losslessness: raw is the system of record, nothing is dropped

Freshness and completeness are worthless if the transform quietly loses data. Two rules enforce
that it doesn't:

- **Raw is immutable and complete.** Every artifact the API returns is stored **byte-for-byte** and
  never modified or deleted: each Jahresabschluss XML *and* PDF, plus the raw master-extract and
  search responses. If the API gave it to us, it exists verbatim in the raw layer. Downstream
  layers are projections *of* that record, never a replacement for it.
- **Every field is accounted for.** Parsing maps each source position to a canonical name via a
  position-mapping table; any code we do not recognize is kept under a **passthrough** keyed by its
  code and label, so two different free-text slots that reuse the same tag both survive rather than
  overwriting each other. An automated **layer-completeness test** walks every leaf field, position,
  and code at each layer and **fails the build** on any silent loss — including a raw-to-parsed check
  that every value-bearing element is either mapped to a canonical or captured in passthrough, with
  **zero unaccounted**. The master-extract path is audited the same way; that audit is what caught
  dropped fields like the court, a manager role, and the EU identifier before they could reach
  production.

So "we have the data" is not a claim — it is a test that has to stay green.

## 4. Reconciliation: prove the daily feed didn't miss anything

Change feeds drift. The quarterly full sweep doubles as the audit: it re-enumerates the whole
register and emits a **drift report** — the companies the sweep had to newly add (a Neueintragung
the daily feed missed) or newly mark deleted (a missed Löschung). On a healthy system that report
is short; a growing report is an early warning. It is the mechanism that lets us say the mirror is
complete *and* show the evidence.

## 5. Why this generalizes

None of the above is Austria-specific in shape. Any register that exposes a searchable surface and
a change signal fits the same three-part pattern — full resumable enumeration for completeness, a
watermarked delta for freshness, and a byte-for-byte raw record with an enforced no-loss transform.
The same architecture is how the approach extends to other jurisdictions (for example a German
Handelsregister / Unternehmensregister mirror): swap the source adapter, keep the guarantees.

## What we deliberately do not publish

The **runnable collector** — the exact enumeration strategy against the metered official API, the
retry/back-off and dead-letter handling, the cost tuning, and the parsing/consolidation internals —
stays private. That is the operational core, and it enumerates a paid official interface; publishing
a turnkey hammer for that interface would be irresponsible toward the source. What is public is the
*shape* of the system and the MCP serving layer, so an engineer can understand, trust, and learn
from how the dataset is built — without being handed the machine that builds it.
