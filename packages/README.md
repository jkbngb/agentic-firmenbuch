# `packages/` — shared, source-agnostic code

These packages carry **zero** Firmenbuch/UGB/ÖNACE knowledge and are reusable by any
product in the `agentic-first` monorepo (the Austrian pipeline today, a second register
tomorrow). The AT-specific stage packages live under
[`products/agentic-firmenbuch/packages/`](../products/agentic-firmenbuch/README.md).

The **[repo root README](../README.md) is the master index**; this file is the shared-package guide.

| Directory | Package | Role |
|---|---|---|
| [`core`](core/README.md) | `fbl_core` | source-agnostic contracts: lineage/meta (`Meta`, `Stage`) + metric series (`MetricSeries`, `Trend`), config, storage clients (Blob/Cosmos + in-memory fakes) |
| [`auth`](auth/README.md) | `fbl_auth` | signup, token issue/hash/validate, OAuth/DCR, rate-limit + metering, `00_accounts`, ACS email |

## The reuse boundary (hard rule)

A shared package **must not** import anything product-specific. The dependency arrow only ever
points **product → shared** (e.g. `fbl_core_at` → `fbl_core`), never back. The precise
1:1 / adapt / product-local classification per package is the **reuse table (Appendix R)** in the
[Technische Spezifikation](../docs/specs/Technische_Spezifikation.md).

> Note: `derive` and `mcp_server` are algorithmically source-agnostic but currently bind to
> AT-shaped domain models, so they live in the AT product for now, tagged *promotion-candidate*
> — they move here once the domain models are abstracted (a V2 pass). See Appendix R.

## Conventions

- `ruff` + `mypy --strict` + `pytest` must pass for the whole workspace (`uv run … packages products`).
- Each README links **up** to the root and **across** to related packages.

---
↑ [Repo root](../README.md) · Specs: [Technische Spezifikation](../docs/specs/Technische_Spezifikation.md)
