"""T11 search side — single-signal score sort, weighted rank_by mix, unknown-field rejection."""

from __future__ import annotations

from typing import Any

import pytest

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import RankSignal, SearchFilters, Sort
from fbl_mcp_server import BadRequest, McpService

PRESENTED = "10_presentation"


def _doc(
    fnr: str, scores: dict[str, Any] | None, bilanzsumme: float | None = 100.0
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": f"Firma {fnr}", "legal_form": "GES", "status": "active"},
        "financials": {"latest": {"bilanzsumme": bilanzsumme}},
        "provenance": {"data_version": 1},
    }
    if scores is not None:
        d["scores"] = scores
    return d


def _svc(docs: list[dict[str, Any]]) -> tuple[McpService, str]:
    cosmos = InMemoryCosmosStore()
    for d in docs:
        cosmos.upsert(PRESENTED, d)
    token = signup("u@example.test", cosmos).token
    return McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000)), token


def test_single_signal_sort_places_scoreless_docs_last() -> None:
    svc, token = _svc(
        [
            _doc("hi", {"growth": 90.0, "basis": ["x"]}),
            _doc("lo", {"growth": 30.0, "basis": ["x"]}),
            _doc("none", None),  # no scores → bucket B, must still appear, after the ranked ones
        ]
    )
    res = svc.search_companies(token, SearchFilters(), Sort(field="score_growth"))
    order = [r["fnr"] for r in res["results"]]
    assert order == ["hi", "lo", "none"]  # scoreless doc kept, ranked last (#32 invariant holds)
    assert res["total"] == 3


def test_weighted_mix_hand_computed_order() -> None:
    # weights: growth 0.7, solidity 0.3.
    #   A: 0.7*100 + 0.3*0   = 70
    #   B: 0.7*50  + 0.3*90  = 62
    #   C: 0.7*0   + 0.3*100 = 30
    svc, token = _svc(
        [
            _doc("A", {"growth": 100.0, "solidity": 0.0, "basis": ["x"]}),
            _doc("B", {"growth": 50.0, "solidity": 90.0, "basis": ["x"]}),
            _doc("C", {"growth": 0.0, "solidity": 100.0, "basis": ["x"]}),
        ]
    )
    res = svc.search_companies(
        token,
        SearchFilters(),
        Sort(
            rank_by=[
                RankSignal(signal="growth", weight=0.7),
                RankSignal(signal="solidity", weight=0.3),
            ]
        ),
    )
    assert [r["fnr"] for r in res["results"]] == ["A", "B", "C"]


def test_weighted_mix_renormalizes_on_missing_signal() -> None:
    # D has only solidity=80 → scored on the present weight alone (80), not penalized to 0.7*0+…
    svc, token = _svc(
        [
            _doc("D", {"solidity": 80.0, "basis": ["x"]}),
            _doc("E", {"growth": 60.0, "solidity": 60.0, "basis": ["x"]}),
        ]
    )
    res = svc.search_companies(
        token,
        SearchFilters(),
        Sort(
            rank_by=[
                RankSignal(signal="growth", weight=0.5),
                RankSignal(signal="solidity", weight=0.5),
            ]
        ),
    )
    # D renormalized = 80; E = 0.5*60+0.5*60 = 60 → D first.
    assert [r["fnr"] for r in res["results"]] == ["D", "E"]


def test_unknown_sort_field_is_bad_request() -> None:
    svc, token = _svc([_doc("1a", {"growth": 10.0, "basis": ["x"]})])
    # Raised as BadRequest (code "bad_request") — FastMCP surfaces it to the client. It used to
    # silently drop ordering; now the caller gets the list of valid fields.
    with pytest.raises(BadRequest) as exc:
        svc.search_companies(token, SearchFilters(), Sort(field="not_a_field"))
    assert exc.value.code == "bad_request"
    assert "score_growth" in exc.value.message
