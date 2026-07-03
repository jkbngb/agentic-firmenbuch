# `products/` — per-source products

Each subdirectory is one **source-specific** product built on the shared, source-agnostic
[`packages/`](../packages/README.md). A product owns its register client, its parser, its
domain mappings/models and its serving/orchestration wiring; it never leaks that knowledge back
into `packages/`.

| Product | Status | Source |
|---|---|---|
| [🇦🇹 `agentic-firmenbuch`](agentic-firmenbuch/README.md) | **live** | Austrian Firmenbuch HVD (JustizOnline, CC BY 4.0) |

Additional source-specific products are added in their **own separate repositories** that consume
`packages/{core,auth}` as a dependency — they are not scaffolded in this repo. The generic recipe
and the mirror-the-existing-structure template are in
[docs/monorepo/ADDING_A_PRODUCT.md](../docs/monorepo/ADDING_A_PRODUCT.md). Region scoping (a specific
state/Bundesland/DACH) is a **filter/tier inside a product**, not its own slot.

---
↑ [Repo root (agentic-first)](../README.md)
