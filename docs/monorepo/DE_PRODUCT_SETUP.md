# Germany product (`agentic-unternehmensregister`) — setup as a separate private repo

**Status:** not built. This document is the plan + the empty-slot template. No Germany code
lives in this public repo (owner decision — see below).

## Why a separate private repo (not a folder here)

This repo (`github.com/jkbngb/agentic-firmenbuch`) is **public**. Scaffolding a folder literally
named `products/agentic-unternehmensregister/` would publicly announce a second-market product in
git history, even if later removed. Per the owner decision (Technische Spezifikation §3.5 / point 6),
the Germany product therefore lives in its **own private repo** and pulls the shared, source-agnostic
packages from here as a dependency. Everything under `packages/` (`fbl_core`, `fbl_auth`) is designed
to be consumed this way — see the reuse table (**Appendix R**).

> **Scoping note:** Bayern / DE / DACH selection is a **filter or tier inside the product**, not its
> own slot. Do not fragment the product per region.

## How the private repo consumes `packages/`

Two viable mechanisms; pick one.

### Option A — git submodule (recommended, zero publishing)

Vendour this repo's shared packages into the private repo as a submodule, then declare uv path
dependencies against them:

```bash
# in the private agentic-unternehmensregister repo
git submodule add https://github.com/jkbngb/agentic-firmenbuch shared-upstream
```

```toml
# private repo pyproject.toml (uv workspace)
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
fbl-core = { path = "shared-upstream/packages/core", editable = true }
fbl-auth = { path = "shared-upstream/packages/auth", editable = true }
```

Bump the shared code with `git -C shared-upstream pull`. The submodule pins an exact commit, so
builds stay reproducible. Only `packages/**` is consumed; the AT product tree is ignored.

### Option B — private package index

Publish `fbl-core` / `fbl-auth` as wheels to a private index (Azure Artifacts / GitHub Packages)
from this repo's CI, and depend on them by version in the private repo. More moving parts (a release
pipeline, versioning) — only worth it once the shared surface is stable.

Either way the **hard rule** holds: the arrow points **DE product → shared**; the shared packages
never learn anything German. If the DE product needs something the shared package can't provide
source-agnostically, that's a signal to abstract it in `packages/` (benefiting AT too), not to fork.

## Empty-slot template (create these in the private repo)

Mirror the AT product's shape so the two are navigable the same way. Layer numbers match the
pipeline (`90 → 10`); import names would be e.g. `fbl_de_parse`.

```
agentic-unternehmensregister/          # private repo root (own uv workspace)
├── pyproject.toml                      # members = packages/*; sources → shared via Option A/B
├── shared-upstream/                    # git submodule of agentic-firmenbuch (packages/ only used)
└── packages/
    ├── source/     (fbl_de_source)     # Handelsregister/Unternehmensregister client (RegisterSource impl)
    ├── parse/      (fbl_de_parse)      # German filing XML/PDF → canonical positions
    ├── mapping/    (fbl_de_mapping)    # HGB-DE position taxonomy + domain models (DE counterpart of core_at)
    └── app/        (fbl_de_app)        # orchestration + MCP serving entrypoint for the DE product
```

Each package gets a `README.md` stub with the standard header:

```markdown
# `<pkg>` — <one line>

**Layer** | <99_registry | 90_raw | 70_parsed | … | un-numbered>
**Reads** | <shared fbl_core / previous layer store>
**Writes** | <this layer's store>

TODO — wird aus den DE-docs/specs gefüllt (Handelsregister/Unternehmensregister).
Consumes shared `fbl_core` / `fbl_auth` from `shared-upstream/packages/` (see Appendix R).
```

## What is reused as-is vs rebuilt

Straight from Appendix R (Technische Spezifikation): `fbl_core` (lineage/meta + metric contracts,
config, storage) and `fbl_auth` (signup/token/metering/OAuth) are **1:1 shared**. The
`RegisterSource` Protocol, the merge/supersede consolidation framework, the ratio/growth math, and
the FastMCP/OAuth serving framing are **reusable patterns** to copy-and-adapt once `derive`/`mcp_server`
are promoted out of the AT product. Everything modelling a specific filing/company (the `core_at`
counterpart) is **rebuilt for German law**.

---
↑ [Repo root (agentic-first)](../../README.md) · [products/](../../products/README.md) · Reuse table: [Technische Spezifikation Appendix R](../specs/Technische_Spezifikation.md)
