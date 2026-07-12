"""T10 — name-relevance re-ranking: when finding a company by name, match quality dominates the
numeric sort, so NOVOMATIC AG (no Bilanzsumme) still outranks a micro subsidiary that merely
contains the word."""

from __future__ import annotations

from typing import Any

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService
from fbl_mcp_server.service.search import _name_match_score

PRESENTED = "10_presentation"


def test_name_match_score_ordering() -> None:
    # exact > prefix > word-boundary > substring
    assert _name_match_score("novomatic", "Novomatic")[0] == 4
    assert _name_match_score("novomatic", "Novomatic AG")[0] == 3
    assert _name_match_score("novomatic", "Astra Novomatic Betting")[0] == 2
    assert _name_match_score("matic", "Novomatic AG")[0] == 1
    # tie inside a tier: the shorter (more fully covered) name wins on the ratio component
    prefix_short = _name_match_score("novo", "Novo AG")
    prefix_long = _name_match_score("novo", "Novo Nordisk Pharma Handels GmbH")
    assert prefix_short[0] == prefix_long[0] == 3 and prefix_short[1] > prefix_long[1]


def _doc(fnr: str, name: str, bilanzsumme: float | None) -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": name, "legal_form": "GES", "status": "active"},
        "financials": {"latest": {"bilanzsumme": bilanzsumme}},
        "provenance": {"data_version": 1},
    }


def test_novomatic_ag_ranks_above_subsidiary_despite_no_bilanzsumme() -> None:
    cosmos = InMemoryCosmosStore()
    # The micro subsidiary HAS a Bilanzsumme; the AG has none → default numeric sort buries the AG.
    cosmos.upsert(PRESENTED, _doc("1a", "NOVOMATIC Sports Betting Solutions GmbH", 114_000.0))
    cosmos.upsert(PRESENTED, _doc("2b", "NOVOMATIC AG", None))
    token = signup("u@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))

    res = svc.search_companies(token, SearchFilters(name="novomatic"))
    order = [r["fnr"] for r in res["results"]]
    assert order[0] == "2b"  # NOVOMATIC AG first — prefix match, more fully covered name
    assert res["total"] == 2  # total stays exact


def test_exact_match_beats_prefix() -> None:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(PRESENTED, _doc("1a", "Red Bull GmbH", 5_000_000.0))
    cosmos.upsert(PRESENTED, _doc("2b", "Red Bull", None))
    token = signup("u@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    res = svc.search_companies(token, SearchFilters(name="red bull"))
    assert res["results"][0]["fnr"] == "2b"  # exact casefold match wins over the larger GmbH


def test_non_name_search_keeps_numeric_sort() -> None:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(PRESENTED, _doc("1a", "Small GmbH", 100_000.0))
    cosmos.upsert(PRESENTED, _doc("2b", "Big GmbH", 900_000.0))
    token = signup("u@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    # No name filter → screening sort (bilanzsumme desc) unchanged.
    res = svc.search_companies(token, SearchFilters())
    assert [r["fnr"] for r in res["results"]] == ["2b", "1a"]
