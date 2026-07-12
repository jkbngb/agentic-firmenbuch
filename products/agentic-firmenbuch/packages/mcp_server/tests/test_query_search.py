"""T14 (shippable subset) — the lexical `query` leg + match_reason.

The vector/FTS hybrid is gated on infra the owner enables; until then `query` is a lexical
substring-OR over name + activity, combinable with every structured filter, with match_reason
naming the leg(s) that hit."""

from __future__ import annotations

from typing import Any

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService

PRESENTED = "10_presentation"


def _doc(fnr: str, name: str, desc: str | None, *, bundesland: str = "W") -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": name, "legal_form": "GES", "status": "active"},
        "location": {"bundesland": bundesland},
        "company": {"description": desc},
        "financials": {"latest": {"bilanzsumme": 100.0}},
        "provenance": {"data_version": 1},
    }


def _svc(docs: list[dict[str, Any]]) -> tuple[McpService, str]:
    cosmos = InMemoryCosmosStore()
    for d in docs:
        cosmos.upsert(PRESENTED, d)
    token = signup("u@example.test", cosmos, tier="pro").token
    return McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000)), token


def test_query_matches_name_or_activity() -> None:
    svc, token = _svc(
        [
            _doc("1a", "Bau Meier GmbH", "Hochbau und Tiefbau"),
            _doc("2b", "Software AG", "Anlagenbau und Montage"),  # matches on activity, not name
            _doc("3c", "Handels GmbH", "Lebensmittelhandel"),  # no match
        ]
    )
    res = svc.search_companies(token, SearchFilters(query="bau"))
    fnrs = {r["fnr"] for r in res["results"]}
    assert fnrs == {"1a", "2b"} and res["total"] == 2


def test_match_reason_names_the_leg() -> None:
    svc, token = _svc(
        [
            _doc("1a", "Bau Meier GmbH", "Hochbau"),  # both name + activity
            _doc("2b", "Software AG", "Anlagenbau"),  # activity only
        ]
    )
    res = svc.search_companies(token, SearchFilters(query="bau"))
    by = {r["fnr"]: r for r in res["results"]}
    assert by["1a"]["match_reason"] == "text: name + Tätigkeit match"
    assert by["2b"]["match_reason"] == "text: Tätigkeit match"


def test_query_combines_with_structured_filters() -> None:
    svc, token = _svc(
        [
            _doc("1a", "Bau Wien GmbH", "Hochbau", bundesland="W"),
            _doc("2b", "Bau Graz GmbH", "Hochbau", bundesland="St"),
        ]
    )
    res = svc.search_companies(token, SearchFilters(query="bau", bundesland="Wien"))
    assert [r["fnr"] for r in res["results"]] == ["1a"]  # query AND region


def test_query_no_match_offers_relaxation_with_other_filter() -> None:
    svc, token = _svc([_doc("1a", "Bau GmbH", "Hochbau", bundesland="W")])
    # query matches but region doesn't → 0 hits, relaxation should point at bundesland.
    res = svc.search_companies(token, SearchFilters(query="bau", bundesland="Tirol"))
    assert res["total"] == 0
    assert any(r["dropped"] == "bundesland" for r in res["relaxations"])
