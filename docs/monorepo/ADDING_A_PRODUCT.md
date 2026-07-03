# Adding a second product to the `agentic-first` monorepo

The `agentic-first` split (Technische Spezifikation §3, §3.5, Appendix R) exists so a **second,
source-specific product** can be added without touching the existing pipeline. This document is
the generic recipe. It intentionally names no specific second source — a new product is created in
its **own separate repository** and consumes the shared packages from here.

## The rule

- Shared, source-agnostic code lives in `packages/` (`fbl_core`, `fbl_auth`). It carries **zero**
  source-specific knowledge.
- Each product owns its register client, parser, domain mappings/models, and serving/orchestration
  wiring under `products/<product>/`.
- The dependency arrow points **product → shared**, never back. If a product needs something a
  shared package can't provide source-agnostically, abstract it in `packages/` (benefiting every
  product) rather than forking.
- A product is created in a **separate repository** unless the owner decides otherwise; it consumes
  `packages/{core,auth}` via git submodule (path dependency) or a private package index.

## Mirror the existing product's structure exactly

A new product uses the **same directory names and the same layer numbers** as
[`products/agentic-firmenbuch/`](../../products/agentic-firmenbuch/README.md), so anyone who knows
one product is immediately oriented in the other. Only the source-specific domain package
(`core_<x>`) and the register client differ in name:

```
products/<product>/                    # (or a separate repo root with the same shape)
├── packages/
│   ├── core_<x>/    (fbl_core_<x>)     # domain models + position taxonomy + classification (counterpart of core_at)
│   ├── <source>_client/               # register client — implements the shared RegisterSource seam
│   ├── 99_registry/                   # LAYER 99_registry — company catalog + watermark
│   ├── 90_ingest/                     # LAYER 90_raw — enumeration + change feed + raw download
│   ├── 70_parse/                      # LAYER 70_parsed — raw filing → canonical positions
│   ├── 50_consolidate/                # LAYER 50_consolidated — merge per company
│   ├── 30_derive/                     # LAYER 30_derived — ratios/growth (reuses the AT math once promoted)
│   ├── 10_present/                    # LAYER 10_presentation — gated served doc
│   ├── orchestration/                 # the --mode Job entrypoint that wires the stages
│   └── mcp_server/                    # FastMCP app + tools (reuses fbl_auth)
└── tests/                             # integration tests + golden fixtures
```

The **layer-numbered stage names are identical across products** (they name a data layer, not a
source). Each package keeps a `README.md` with the standard `Layer | reads | writes` header. Region
scoping (e.g. a specific Bundesland/state/DACH) is a **filter or tier inside a product**, never its
own slot — do not fragment.

### Separate-repo wiring (git submodule)

```bash
git submodule add https://github.com/jkbngb/agentic-firmenbuch shared-upstream
```
```toml
# new product's pyproject.toml
[tool.uv.sources]
fbl-core = { path = "shared-upstream/packages/core", editable = true }
fbl-auth = { path = "shared-upstream/packages/auth", editable = true }
```

## What is reused vs rebuilt (Appendix R)

`fbl_core` (lineage/meta + metric contracts, config, storage) and `fbl_auth` (signup/token/metering/
OAuth) are **1:1 shared**. The `RegisterSource` Protocol, the merge/supersede consolidation
framework, the ratio/growth math, and the FastMCP/OAuth serving framing are **reusable patterns**
(the last two become truly shared once `30_derive`/`mcp_server` are promoted out of the AT product).
Everything modelling a specific filing/company is **rebuilt for that source's law**.

---
↑ [Repo root (agentic-first)](../../README.md) · [products/](../../products/README.md) · Reuse table: [Technische Spezifikation Appendix R](../specs/Technische_Spezifikation.md)
