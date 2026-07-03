"""Deterministic (no-LLM) playground backend + guards (Distribution §13).

The full chat UI (``website/playground.html``) talks to ``/api/playground``. This module is
the **structured fallback mode**: it parses a German question into :class:`SearchFilters`,
runs the existing ``service.search_companies`` query layer, and returns company cards — with
**zero LLM cost**. The **LLM tool-calling mode** is wired only *after* the MCP-contract
checkpoint (Distribution §13a); a config flag (``playground_llm_enabled``) will switch modes
behind the same UI.

Guards (anonymous users = hostile): kill-switch, Turnstile gate, per-visitor + per-IP +
global daily caps (Cosmos counters), output cap, and **no chat-history persistence**.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fbl_auth import hash_token
from fbl_auth.metrics import bump_metric
from fbl_core.storage import CosmosStoreLike
from fbl_core_at.models.mcp import SearchFilters, Sort

from . import service

logger = logging.getLogger(__name__)

# (token, remote_ip) -> bool
TurnstileVerifier = Callable[[str, str | None], bool]
# (filters, sort) -> (total_matches, list of company-card dicts). Total is the full
# match count so the LLM (and deterministic UI) can report it; rows are a preview.
Searcher = Callable[[SearchFilters, Sort | None], tuple[int, list[dict[str, Any]]]]

_ACCOUNTS = "00_accounts"

# Austrian Bundesländer (lower-case match → canonical name as served).
_BUNDESLAENDER = {
    "wien": "Wien",
    "niederösterreich": "Niederösterreich",
    "niederoesterreich": "Niederösterreich",
    "oberösterreich": "Oberösterreich",
    "oberoesterreich": "Oberösterreich",
    "steiermark": "Steiermark",
    "tirol": "Tirol",
    "kärnten": "Kärnten",
    "kaernten": "Kärnten",
    "salzburg": "Salzburg",
    "vorarlberg": "Vorarlberg",
    "burgenland": "Burgenland",
}


def parse_intent(text: str) -> tuple[SearchFilters, list[str]]:
    """Parse a German question into SearchFilters. Returns (filters, notes on unsupported bits)."""
    t = (text or "").lower()
    f = SearchFilters()
    notes: list[str] = []

    for needle, canonical in _BUNDESLAENDER.items():
        if needle in t:
            f.bundesland = canonical
            break

    if "gmbh" in t or "ges.m.b.h" in t:
        f.legal_form = "GmbH"
    if re.search(r"\bag\b|aktiengesellschaft", t):
        f.legal_form = "AG"
    if "aktiv" in t:
        f.status = "active"
    if any(w in t for w in ("gelöscht", "geloescht", "inaktiv", "aufgelöst")):
        f.status = "inactive"

    # "Bilanzsumme über 5 Mio." / "mehr als 5 Millionen" / "> 5 mio euro"
    m = re.search(r"(?:über|ueber|mehr als|größer|groesser|>)\s*([\d.,]+)\s*(mio|million|mrd)?", t)
    if m and (
        "bilanz" in t
        or "umsatz" in t
        or "mio" in (m.group(2) or "")
        or "million" in (m.group(2) or "")
    ):
        val = float(m.group(1).replace(".", "").replace(",", "."))
        unit = m.group(2) or ""
        if "mrd" in unit:
            val *= 1_000_000_000
        elif "mio" in unit or "million" in unit:
            val *= 1_000_000
        if "umsatz" in t:
            f.revenue_min = val
        else:
            f.bilanzsumme_min = val

    # Eigenkapitalquote: "hohe Eigenkapitalquote" → ≥40%; "über 30%" → that threshold.
    if "eigenkapital" in t:
        pm = re.search(r"([\d]{1,3})\s*%", t)
        f.equity_ratio_min = (int(pm.group(1)) / 100) if pm else 0.40

    if any(w in t for w in ("wachs", "wächst", "waechst", "wachstum")):
        f.growth_profile = "growing"

    # GF-Alter / Nachfolge → gf_age_min (primary Geschäftsführer current age).
    gf_ctx = any(w in t for w in ("geschäftsführer", "geschaeftsfuehrer", " gf ", "nachfolge"))
    am = re.search(r"(?:über|ueber|älter als|aelter als|ab)\s*(\d{2})\s*(?:jahre|jahren|j)?", t)
    if gf_ctx and am:
        f.gf_age_min = int(am.group(1))
    elif "nachfolge" in t:
        f.gf_age_min = 60  # succession default when no explicit age is given
    return f, notes


def _default_searcher(cosmos: CosmosStoreLike, max_results: int) -> Searcher:
    def search(
        filters: SearchFilters, sort: Sort | None = None
    ) -> tuple[int, list[dict[str, Any]]]:
        res = service.search_companies(cosmos, filters, sort=sort, page=1, page_size=max_results)
        return res.total, [c.model_dump(mode="json") for c in res.results]

    return search


def _within_cap(cosmos: CosmosStoreLike, label: str, limit: int, now: datetime) -> bool:
    """Best-effort daily counter in 00_accounts. True if still under the limit."""
    day = now.strftime("%Y-%m-%d")
    key = hash_token(f"pgcap:{label}:{day}")
    doc = cosmos.get(_ACCOUNTS, key) or {
        "id": key,
        "token_hash": key,
        "kind": "pg_cap",
        "label": label,
        "day": day,
        "count": 0,
    }
    if int(doc.get("count", 0)) >= limit:
        return False
    doc["count"] = int(doc.get("count", 0)) + 1
    cosmos.upsert(_ACCOUNTS, doc)
    return True


def _clean_history(raw: Any) -> list[dict[str, Any]]:
    """Sanitize client-supplied chat history → at most the last 6 user/assistant text turns."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for m in raw[-6:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content[:2000]})
    return out


def playground_request(
    payload: dict[str, Any],
    ip: str | None,
    visitor_id: str,
    cosmos: CosmosStoreLike,
    *,
    enabled: bool = True,
    turnstile_secret: str | None = None,
    turnstile_verifier: TurnstileVerifier | None = None,
    per_visitor_day: int = 10,
    per_ip_day: int = 30,
    global_day: int = 2000,
    max_results: int = 8,
    searcher: Searcher | None = None,
    llm_enabled: bool = False,
    anthropic_api_key: str | None = None,
    llm_model: str = "claude-haiku-4-5-20251001",
    llm_max_tokens: int = 900,
    now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """Handle one playground message in deterministic mode. Returns (status, payload).

    No message is ever persisted (no chat history). Caps are checked global → IP → visitor so
    a blocked request doesn't consume a visitor's quota unnecessarily.
    """
    now = now or datetime.now(UTC)
    if not enabled:
        return 503, {"error": "disabled", "message": "Der Playground ist gerade nicht verfügbar."}

    message = str(payload.get("message", "")).strip()
    if not message:
        return 400, {"error": "empty", "message": "Bitte gib eine Frage ein."}
    if len(message) > 500:
        return 400, {"error": "too_long", "message": "Bitte kürze deine Frage (max. 500 Zeichen)."}

    if turnstile_secret:
        ok = (
            turnstile_verifier(str(payload.get("turnstile_token", "")), ip)
            if turnstile_verifier
            else False
        )
        if not ok:
            return 400, {
                "error": "turnstile_failed",
                "message": "Sicherheitsprüfung fehlgeschlagen.",
            }

    if not _within_cap(cosmos, "global", global_day, now):
        return 429, {
            "error": "global_cap",
            "message": "Das Tageslimit ist erreicht. Bitte morgen erneut.",
        }
    if ip and not _within_cap(cosmos, f"ip:{ip}", per_ip_day, now):
        return 429, {"error": "ip_cap", "message": "Tageslimit für deine Verbindung erreicht."}
    if not _within_cap(cosmos, f"v:{visitor_id}", per_visitor_day, now):
        return 429, {
            "error": "visitor_cap",
            "message": f"Du hast das Test-Limit von {per_visitor_day} Fragen pro Tag erreicht. "
            "Hol dir einen kostenlosen API-Key für unbegrenzte Nutzung.",
        }

    bump_metric(cosmos, "playground_queries", now=now)  # privacy-friendly daily counter
    search = searcher or _default_searcher(cosmos, max_results)

    # LLM mode: Claude does tool-calling against the same search layer and writes a summary.
    # Any failure falls back to the deterministic parser so the playground never hard-fails.
    if llm_enabled and anthropic_api_key:
        try:
            from .playground_llm import llm_answer

            payload = llm_answer(
                message,
                search,
                api_key=anthropic_api_key,
                model=llm_model,
                max_tokens=llm_max_tokens,
                max_results=max_results,
                history=_clean_history(payload.get("history")),
            )
            return 200, payload
        except Exception:  # never let an LLM error break the demo → deterministic fallback
            logger.exception("playground LLM mode failed; falling back to deterministic")

    filters, notes = parse_intent(message)
    total, results = search(filters, None)
    results = results[:max_results]
    if not results:
        notes.append(
            "Aktuell sind noch keine passenden Unternehmen geladen — die Datenbank wird gerade "
            "befüllt."
        )
    return 200, {
        "mode": "deterministic",
        "interpretation": filters.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
        "total_matches": total,
        "results": results,
        "notes": notes,
    }
