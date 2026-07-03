# `products/` — per-source products

Each subdirectory is one **source-specific** product built on the shared, source-agnostic
[`packages/`](../packages/README.md). A product owns its register client, its parser, its
domain mappings/models and its serving/orchestration wiring; it never leaks that knowledge back
into `packages/`.

| Product | Status | Source |
|---|---|---|
| [🇦🇹 `agentic-firmenbuch`](agentic-firmenbuch/README.md) | **live** | Austrian Firmenbuch HVD (JustizOnline, CC BY 4.0) |
| 🇩🇪 `agentic-unternehmensregister` | separate **private** repo (not here) | German Handelsregister / Unternehmensregister |

The Germany product is **not** scaffolded in this public monorepo (owner decision): it lives in
its own private repo that consumes `packages/{core,auth}` as a dependency. The mechanism and the
empty-slot template are in [docs/monorepo/DE_PRODUCT_SETUP.md](../docs/monorepo/DE_PRODUCT_SETUP.md).
Bundesland/DACH scoping is a **filter/tier inside a product**, not its own slot.

---
↑ [Repo root (agentic-first)](../README.md)
