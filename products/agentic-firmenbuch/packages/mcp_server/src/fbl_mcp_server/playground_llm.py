"""LLM-mode playground backend (Distribution §13).

A **cheap Claude model** (Haiku by default) does tool-calling against the *same* search
layer the deterministic mode uses: the model turns a free-text German question into a
``search_companies`` tool call, we run it over ``10_presentation`` and feed real rows back,
and the model writes a short German summary. The answer carries **both** the structured rows
(rendered as a table by the UI) and the prose ``summary``.

Cost is bounded three ways: the daily caps in :mod:`playground` gate how many calls run at
all, ``max_tokens`` caps each answer, and the tool-call loop is capped at a few rounds. The
Anthropic key is server-side only (Key Vault) — visitors never see or supply it.

If anything fails (no key, API error), the caller falls back to the deterministic parser, so
the playground never hard-fails on an LLM problem.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, cast

from fbl_core_at.models.mcp import SearchFilters, Sort

logger = logging.getLogger(__name__)

# (filters, sort) -> (total_matches, list of company-card dicts capped at max_results).
# Total is the FULL match count so the LLM can report it accurately even when only a page
# of rows is fed back (the demo caps at 8 rows but a query may match thousands).
Searcher = Callable[[SearchFilters, Sort | None], tuple[int, list[dict[str, Any]]]]

_MAX_TOOL_ROUNDS = 3  # bound the tool-use loop (cost guard)

_SYSTEM = (
    "Du bist der Assistent des Agentic-Firmenbuch-MCP-Servers. Du beantwortest "
    "AUSSCHLIESSLICH Fragen zu österreichischen Firmen- und Bilanzdaten aus dem offiziellen "
    "Firmenbuch (Quelle: Österreichisches Firmenbuch / BMJ). "
    "Nutze IMMER das Werkzeug search_companies, um echte Daten zu holen — erfinde niemals "
    "Firmen, Zahlen oder Kennzahlen. Für Ranglisten (z. B. 'höchster Umsatz', 'größte "
    "Unternehmen', 'Top 5') setze das Feld sort; Umsatz (revenue) liegt nur mit "
    "veröffentlichter GuV vor, kombiniere Umsatz-Ranking daher mit has_guv_latest=true. "
    "Für die Suche nach einem konkreten Firmennamen nutze name. "
    "Für eine Stadt (z. B. 'aus Graz', 'in Linz') nutze city — NICHT bundesland; "
    "Städtenamen sind keine Bundesländer. "
    "Fasse das Ergebnis in HÖCHSTENS 2–3 knappen Sätzen auf Deutsch zusammen: die Trefferzahl, "
    "den Spitzenreiter mit seiner wichtigsten Kennzahl und ggf. eine kurze Einordnung. "
    "WICHTIG: Die Trefferzahl IST das Feld 'total_matches' aus dem Tool-Ergebnis (NICHT die "
    "Länge der zurückgelieferten 'results'-Liste – die ist nur die angezeigte Vorschau, "
    "siehe 'shown'). Bei sehr vielen Treffern (z. B. tausenden) sage etwa 'rund 1.400 GmbHs …'. "
    "Zähle NICHT alle Treffer einzeln auf — die strukturierte Tabelle zeigt die Vorschau. "
    "Nenne Beträge gerundet (z. B. '11,6 Mrd. €'). Verwende Markdown sparsam (höchstens **fett** "
    "für den Spitzenreiter), keine Aufzählungslisten. Wenn es keine Treffer gibt, sage das "
    "ehrlich und schlage konkret vor, welchen Filter man lockern könnte. "
    "Personennamen werden aus Datenschutzgründen nicht ausgegeben (nur Alter/Geburtsjahr). "
    "Gib keine Anlageberatung und keine Bewertungen/Scores ab. Themenfremde Fragen lehnst du "
    "höflich ab und verweist auf den Zweck (Firmenbuch-Daten)."
)

# Tool input schema = the searchable subset of SearchFilters. Mirrors fbl_core_at.models.mcp.
_SEARCH_TOOL: dict[str, Any] = {
    "name": "search_companies",
    "description": (
        "Filtersuche über das österreichische Firmenbuch. Gibt passende Unternehmen mit "
        "Kennzahlen zurück. Beträge in Euro (z. B. 5 Mio = 5000000)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["active", "inactive", "all"]},
            "name": {
                "type": "string",
                "description": "Teil des Firmennamens (Teilstring-Suche), z. B. 'Rosenbauer'.",
            },
            "legal_form": {
                "type": "string",
                "description": "Rechtsform, z. B. 'GmbH', 'AG', 'KG', 'OG', 'Genossenschaft'.",
            },
            "bundesland": {
                "type": "string",
                "description": "z. B. 'Wien', 'Steiermark', 'Oberösterreich'.",
            },
            "city": {
                "type": "string",
                "description": (
                    "Sitzgemeinde/Stadt (Teilstring), z. B. 'Graz', 'Linz'. Für Städte immer "
                    "city (NICHT bundesland) nutzen — 'Graz' ist eine Stadt, kein Bundesland."
                ),
            },
            "postal_code": {
                "type": "string",
                "description": "PLZ-Präfix, z. B. '8010' (exakt) oder '80' (alle 80xx).",
            },
            "size_gkl": {
                "type": "string",
                "enum": ["W", "K", "M", "G"],
                "description": "UGB-Größenklasse: W=Kleinst, K=Klein, M=Mittel, G=Groß.",
            },
            "bilanzsumme_min": {"type": "number"},
            "bilanzsumme_max": {"type": "number"},
            "equity_ratio_min": {"type": "number", "description": "Eigenkapitalquote 0..1."},
            "equity_ratio_max": {"type": "number"},
            "revenue_min": {"type": "number"},
            "revenue_max": {"type": "number"},
            "employees_min": {"type": "integer"},
            "employees_max": {"type": "integer"},
            "growth_profile": {
                "type": "string",
                "enum": ["shrinking", "stable", "growing", "fast_growing"],
            },
            "has_guv_latest": {"type": "boolean"},
            "last_filing_year_min": {"type": "integer"},
            "gf_age_min": {
                "type": "integer",
                "description": "Aktuelles Mindestalter des Geschäftsführers (Nachfolge-Screen).",
            },
            "sort": {
                "type": "object",
                "description": (
                    "Sortierung für Ranglisten ('höchste/größte/Top'). Das oberste Ergebnis "
                    "ist der Spitzenreiter."
                ),
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": [
                            "bilanzsumme",
                            "revenue",
                            "equity_ratio",
                            "employees",
                            "last_filing_year",
                        ],
                    },
                    "descending": {
                        "type": "boolean",
                        "description": "true = absteigend (höchste zuerst).",
                    },
                },
                "required": ["field"],
            },
        },
        "additionalProperties": False,
    },
}


def _run_tool(
    args: dict[str, Any], searcher: Searcher, max_results: int
) -> tuple[int, list[dict[str, Any]]]:
    """Validate the model's tool args into SearchFilters (+ optional Sort) and run the search.

    Returns ``(total_matches, rows)`` where ``rows`` is capped at ``max_results`` (preview),
    while ``total_matches`` is the FULL match count so the LLM can report it correctly.
    """
    try:
        filters = SearchFilters.model_validate(args)  # extra keys like 'sort' are ignored
    except Exception:  # model passed a stray field/value → keep only what parses
        clean = {k: v for k, v in args.items() if k in SearchFilters.model_fields}
        filters = SearchFilters.model_validate(clean)
    sort: Sort | None = None
    sort_arg = args.get("sort") if isinstance(args, dict) else None
    if isinstance(sort_arg, dict) and sort_arg.get("field"):
        try:
            sort = Sort.model_validate(sort_arg)
        except Exception:  # invalid sort field → ignore, fall back to default ordering
            sort = None
    total, rows = searcher(filters, sort)
    return total, rows[:max_results]


def llm_answer(
    message: str,
    searcher: Searcher,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    max_results: int,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one LLM-mode turn. Returns the playground payload (mode='llm').

    ``history`` is the prior conversation (alternating user/assistant **text** turns) so
    follow-up questions have context. Tool-call rounds from earlier turns are not replayed —
    only the visible text exchange — which keeps the prompt small and the cost bounded.

    Raises on any Anthropic/SDK error so the caller can fall back to deterministic mode.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    messages: list[dict[str, Any]] = [*(history or []), {"role": "user", "content": message}]
    last_results: list[dict[str, Any]] = []
    last_total: int = 0  # full match count from the most recent tool call (≥ len(last_results))
    interpretation: dict[str, Any] = {}

    for _ in range(_MAX_TOOL_ROUNDS):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM,
            tools=cast("Any", [_SEARCH_TOOL]),  # plain dicts; SDK accepts them at runtime
            messages=cast("Any", messages),
        )
        if resp.stop_reason != "tool_use":
            summary = "".join(
                str(getattr(b, "text", ""))
                for b in resp.content
                if getattr(b, "type", None) == "text"
            ).strip()
            return {
                "mode": "llm",
                "summary": summary,
                "results": last_results,
                "total_matches": last_total,
                "interpretation": interpretation,
            }

        messages.append({"role": "assistant", "content": resp.content})
        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            raw_input = getattr(block, "input", {})
            args = dict(raw_input) if isinstance(raw_input, dict) else {}
            interpretation = {k: v for k, v in args.items() if v is not None}
            last_total, last_results = _run_tool(args, searcher, max_results)
            total = last_total  # local alias used in the JSON payload below
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "id", ""),
                    "content": json.dumps(
                        {
                            "total_matches": total,
                            "shown": len(last_results),
                            "results": last_results,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    # Loop exhausted without a final text answer — return the rows with a generic summary.
    logger.warning("playground LLM hit the tool-round cap without a final answer")
    return {
        "mode": "llm",
        "summary": (
            f"{len(last_results)} Treffer gefunden. Verfeinere die Frage für eine "
            "ausführlichere Zusammenfassung."
        ),
        "results": last_results,
        "interpretation": interpretation,
    }
