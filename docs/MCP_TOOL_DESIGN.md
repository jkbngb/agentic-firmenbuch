# MCP Tool Design Standard

Applies to **every MCP server we build** (agentic-firmenbuch and any further product, e.g. the
German counterpart). An agent picks which tool to call purely from the tool's **name, annotations,
input schema, and description**. Public registries (Glama) score exactly these. Good tool definitions
mean agents pick the right tool and pass valid arguments on the first try, which means fewer failed
calls. This is a hard requirement, not cosmetics.

Source rubric: Glama's tool-definition quality score (six dimensions below). Keep this file the single
place we define the standard; the served docstrings are the implementation.

## The rule, per dimension

### 1. Behavior / side-effects  → declare ANNOTATIONS, do not rely on prose
Every tool MUST carry MCP `ToolAnnotations`. Do not make the description carry the whole burden of
"is this safe to call". For our read-only query tools:

```python
from mcp.types import ToolAnnotations
readonly = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

@mcp.tool(annotations=readonly)
def my_tool(...): ...
```

- `readOnlyHint=True` — the tool does not modify state (all our query tools).
- `idempotentHint=True` — same args -> same result (no additional effect on repeat).
- `destructiveHint=False` — never deletes/overwrites.
- `openWorldHint=False` — queries our own served snapshot, not an unbounded external system.

Also state "Read-only." in the first line of the docstring for clients that do not render annotations.
If a tool ever writes, mutates, calls a third party, or costs money, say so explicitly and set the
hints accordingly.

### 2. Parameters  → document every one beyond the schema
The input schema gives types; the description must give **intent**. For each parameter state its
**meaning, an example, constraints, and default**:

- `fnr` (required): Firmenbuchnummer, e.g. `"123456a"` (the `fnr` from a search card).
- `n` (optional, default 10, clamped 1..50): how many peers to return.

Do not leave a parameter unexplained. "0% schema coverage" is the most common score killer.

### 3. Completeness  → describe the output shape, ordering, and edge cases
Even with an output schema, state **what comes back and in what order**, and the notable edge cases:

> Returns up to `n` companies in the SAME size class, nearest by Bilanzsumme (closest first). The
> reference company is excluded. Empty list if the FNR is unknown or has no Bilanzsumme.

Scale the detail to the tool's complexity: a 2-parameter tool needs more than a 0-parameter one.

### 4. Usage guidance  → when to use, when NOT, which sibling instead
Agents often have several tools that could apply. Always disambiguate against siblings:

> Use for one known company. For the complete record use `get_full_record`; for a metric trend use
> `get_company_history`; for many companies use `search_companies`.

### 5. Purpose  → front-loaded first sentence: verb + resource + how it differs
The first sentence states the specific action and resource ("Aggregate statistics for a cohort of
companies") and implicitly or explicitly how it differs from similar tools.

### 6. Conciseness  → front-load, no redundancy, structure it
Front-load the purpose, then short labelled sections (`Parameters:`, then output, then usage). Link to
a human page for exhaustive field detail instead of inlining it. Every sentence earns its place.

## Docstring template

```python
@mcp.tool(annotations=readonly)
def get_thing(ctx: ToolContext, fnr: str, n: int = 10) -> dict[str, Any]:
    """<Verb + resource, one sentence>. Read-only.

    Parameters:
    - fnr (required): <meaning>, e.g. "123456a".
    - n (optional, default 10, clamped 1..50): <meaning>.

    Returns <output shape + ordering + edge cases>.
    Use for <case>; for <other case> use <sibling tool> instead.
    Field reference: <human URL, if any>
    """
```

## Checklist before shipping a tool

- [ ] `ToolAnnotations` set (readOnly / idempotent / destructive / openWorld) and correct.
- [ ] First sentence = verb + resource; "Read-only." stated.
- [ ] Every parameter: meaning + example + constraints + default.
- [ ] Output shape + ordering + empty/None edge cases described.
- [ ] Usage guidance: when to use, when not, which sibling instead.
- [ ] Concise, front-loaded, no redundancy; human detail behind a link.
- [ ] Names unchanged when improving descriptions (renaming a tool is a breaking API change).

## Reference

Glama tool-definition quality scoring (the six dimensions above): the connector's Analytics/Overview
page on glama.ai grades each tool A..F across Purpose, Parameters, Completeness, Behavior, Usage
Guidelines, and Conciseness. This standard is written to score A on all six without renaming tools.
