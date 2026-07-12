"""Tier-2 eval (T13): end-to-end LLM loop measuring ROUNDS (tool calls) per intent.

Optional + budget-capped: needs ANTHROPIC_API_KEY, defaults to Haiku, prints the token bill.
Over ~20 intents Haiku costs roughly <1-2 €. The point is to measure how many search_companies
calls the model needs to satisfy an intent — the number Phase 1 (relaxation + docs) shrinks.
Only run before a release or when explicitly asked; it spends real money.

Imported by scripts/eval_search.py --tier2. Standalone use:
    ANTHROPIC_API_KEY=... uv run python scripts/eval_search.py --ci --tier2 --max-queries 10
"""

from __future__ import annotations

import json
import os
from typing import Any

_TOOL = {
    "name": "search_companies",
    "description": (
        "Search Austrian companies. filters: name (substring), bundesland, oenace_division, "
        "geschaeftszweig (activity text), bilanzsumme_min/max, near {place|postal_code,radius_km}. "
        "sort.field in bilanzsumme|score_growth|score_solidity|score_scale|distance, or "
        "sort.rank_by=[{signal,weight}]. Response: total, results[], and (on 0 hits) relaxations."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filters": {"type": "object"},
            "sort": {"type": "object"},
            "page_size": {"type": "integer"},
        },
    },
}


def _answer_tool() -> dict[str, Any]:
    return {
        "name": "final_answer",
        "description": "Call when you have identified the companies that satisfy the intent.",
        "input_schema": {
            "type": "object",
            "properties": {"fnrs": {"type": "array", "items": {"type": "string"}}},
            "required": ["fnrs"],
        },
    }


def run_tier2(svc: Any, token: str, goldens: list[dict[str, Any]], *, model: str) -> str:
    """Run each intent through a real tool-use loop; report rounds-per-intent + the token bill."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "_skipped: ANTHROPIC_API_KEY not set_"
    from anthropic import Anthropic

    from fbl_core_at.models import SearchFilters, Sort

    client = Anthropic()
    tools = [_TOOL, _answer_tool()]
    rounds: list[int] = []
    in_tok = out_tok = 0

    for g in goldens:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"Intent: {g['intent']}. Use the tools to find the companies.",
            }
        ]
        n_rounds = 0
        for _ in range(8):  # hard cap on rounds per intent
            resp = client.messages.create(
                model=model, max_tokens=1024, tools=tools, messages=messages
            )
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses or any(t.name == "final_answer" for t in tool_uses):
                break
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for t in tool_uses:
                n_rounds += 1 if t.name == "search_companies" else 0
                if t.name == "search_companies":
                    args = t.input
                    filters = SearchFilters(**args.get("filters", {}))
                    sort = Sort(**args["sort"]) if args.get("sort") else None
                    try:
                        out = svc.search_companies(
                            token, filters, sort, page_size=args.get("page_size", 25)
                        )
                    except Exception as exc:
                        out = {"error": str(exc)}
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": t.id,
                            "content": json.dumps(out, ensure_ascii=False)[:4000],
                        }
                    )
            messages.append({"role": "user", "content": results})
        rounds.append(n_rounds)

    avg = sum(rounds) / len(rounds) if rounds else 0.0
    dist = {r: rounds.count(r) for r in sorted(set(rounds))}
    # Haiku list price (approx, EUR): adjust if it moves.
    bill = in_tok / 1e6 * 0.9 + out_tok / 1e6 * 4.5
    return (
        f"intents: {len(rounds)} · avg rounds/intent: {avg:.2f} · rounds distribution: {dist}\n"
        f"tokens: in {in_tok:,} / out {out_tok:,} · est. bill ≈ €{bill:.2f} (model {model})"
    )
