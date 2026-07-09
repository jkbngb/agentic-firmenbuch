"""Plan-based feature gating for the MCP tools (free vs full-access plans).

The plan lives on ``Account.tier`` (values: free, pro, guest, legacy, enterprise).
``free`` is the ONLY feature-limited plan; every other plan has full access. ``guest``
is a time-boxed full-access plan that reverts to ``free`` once ``plan_expires_at`` passes.

This module is pure policy and holds no I/O:
- which tools a free plan may call at all (the rest are Pro-only),
- the monthly cap on ``get_company_details`` for free (the count is read by the caller),
- how the free search card is flattened to basic fields,
- the friendly, agent-readable upgrade responses.

The ``McpService`` wires these decisions onto the tools; the caller supplies the config
values (cap, upgrade URL) and the current usage count, so this module stays free of
Cosmos/Settings imports and is trivially unit-testable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

PLAN_FREE = "free"
PLAN_PRO = "pro"
PLAN_GUEST = "guest"
PLAN_LEGACY = "legacy"

# Tools a free plan may call. ``get_company_details`` is additionally capped per month and
# ``search_companies`` additionally returns a flattened card (see below). Any tool NOT in this
# set is Pro-only for free users.
FREE_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "search_companies",
        "get_company_details",
        "describe_fields",
        "list_sectors",
        "get_coverage",
        "get_my_usage",
    }
)

# The tools gated away from free (named in the upgrade message; equals every tool minus the
# free-allowed set, listed explicitly for a clear, stable contract).
PRO_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "find_peers",
        "get_cohort_summary",
        "get_company_history",
        "get_full_record",
        "get_document",
        "list_events",
        "get_event_stats",
    }
)

# Fields the free search card keeps; every other card field is blanked to ``None``.
# (name, fnr, legal_form, bundesland, postal_code, city, industry_section, Bilanzsumme —
# plus the financial-institution flag, which is a safety caveat, not a premium datapoint.)
FREE_CARD_KEEP: frozenset[str] = frozenset(
    {
        "fnr",
        "name",
        "legal_form",
        "bundesland",
        "postal_code",
        "city",
        "industry_section",
        "bilanzsumme_latest",
        "bilanzsumme_band",
        "is_financial_institution",
    }
)


def _parse_z(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def effective_plan(
    tier: str | None, plan_expires_at: str | None = None, now: datetime | None = None
) -> str:
    """Resolve the plan actually in force. An expired ``guest`` reverts to ``free``."""
    plan = (tier or PLAN_FREE).lower()
    if plan == PLAN_GUEST:
        expires = _parse_z(plan_expires_at)
        if expires is not None and expires <= (now or datetime.now(UTC)):
            return PLAN_FREE
    return plan


def is_full_access(plan: str) -> bool:
    """Every plan except ``free`` has full access to all tools and no monthly cap."""
    return plan != PLAN_FREE


def _gate(tool: str, *, reason: str, upgrade_url: str, detail: str) -> dict[str, Any]:
    """A friendly, agent-readable gate response (NOT an exception).

    Returned in place of the tool's normal payload so the calling agent can read the reason
    and surface the upgrade path without treating it as a hard error.
    """
    return {
        "upgrade_required": True,
        "plan": PLAN_FREE,
        "tool": tool,
        "reason": reason,
        "message": detail,
        "upgrade_url": upgrade_url,
    }


def gate_pro_only(tool: str, upgrade_url: str) -> dict[str, Any]:
    """Gate response for a Pro-only tool called on the free plan."""
    return _gate(
        tool,
        reason="pro_only",
        upgrade_url=upgrade_url,
        detail=(
            f"Das Tool '{tool}' ist Teil von Agentic-Firmenbuch Pro. Im kostenlosen Zugang "
            "stehen Einzelabfragen (get_company_details, monatlich begrenzt) und die Firmensuche "
            "mit Basisdaten zur Verfuegung. Mit Pro sind Screening, Kennzahlen-Historie, "
            "Peer-Vergleiche, Kohorten-Auswertungen, der Volldatensatz und Dokument-Downloads "
            f"freigeschaltet. Details und Freischaltung: {upgrade_url}"
        ),
    )


def gate_details_cap(cap: int, upgrade_url: str) -> dict[str, Any]:
    """Gate response when the free monthly ``get_company_details`` cap is reached."""
    return _gate(
        "get_company_details",
        reason="free_monthly_limit_reached",
        upgrade_url=upgrade_url,
        detail=(
            f"Das kostenlose Kontingent von {cap} vollstaendigen Firmenprofilen pro Monat ist "
            "aufgebraucht. Die Firmensuche mit Basisdaten bleibt nutzbar. Mit Agentic-Firmenbuch "
            f"Pro gibt es unbegrenzte Profile und alle weiteren Tools: {upgrade_url}"
        ),
    )


def flatten_free_card(card: dict[str, Any]) -> dict[str, Any]:
    """Blank every card field that is not part of the free tier (keeps the keys, nulls values)."""
    return {k: (v if k in FREE_CARD_KEEP else None) for k, v in card.items()}


def flatten_free_search_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Reduce a search response's cards to free-tier basic fields + add a plan note."""
    results = resp.get("results")
    if not isinstance(results, list):
        return resp
    out = dict(resp)
    out["results"] = [flatten_free_card(c) if isinstance(c, dict) else c for c in results]
    out["plan_note"] = (
        "Kostenloser Zugang: Basisdaten je Treffer. Vollstaendige Kennzahlen, Kennzahlen-"
        "Historie und Screening-Felder sind in Agentic-Firmenbuch Pro enthalten."
    )
    return out
