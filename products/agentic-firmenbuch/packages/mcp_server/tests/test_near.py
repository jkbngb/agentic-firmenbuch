"""T12 — the `near` radius filter end-to-end over the in-memory store."""

from __future__ import annotations

from typing import Any

import pytest

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.geo import plz_centroid
from fbl_core_at.models import NearFilter, SearchFilters
from fbl_mcp_server import BadRequest, McpService

PRESENTED = "10_presentation"


def _doc(fnr: str, plz: str) -> dict[str, Any]:
    """A doc geo-tagged the way present() tags it (PLZ centroid → location.lat/lng)."""
    lat, lng = plz_centroid(plz)  # type: ignore[misc]
    return {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": f"Firma {fnr}", "legal_form": "GES", "status": "active"},
        "location": {"postal_code": plz, "lat": lat, "lng": lng},
        "financials": {"latest": {"bilanzsumme": 100.0}},
        "provenance": {"data_version": 1},
    }


def _svc(docs: list[dict[str, Any]]) -> tuple[McpService, str]:
    cosmos = InMemoryCosmosStore()
    for d in docs:
        cosmos.upsert(PRESENTED, d)
    token = signup("u@example.test", cosmos, tier="pro").token  # full card (distance_km survives)
    return McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000)), token


def test_radius_includes_near_excludes_far_with_distance_km() -> None:
    svc, token = _svc(
        [
            _doc("close", "4840"),  # Vöcklabruck itself
            _doc("mid", "4865"),  # ~19 km away
            _doc("far", "1010"),  # Vienna, ~200 km away
        ]
    )
    res = svc.search_companies(
        token, SearchFilters(near=NearFilter(place="Vöcklabruck", radius_km=30))
    )
    fnrs = [r["fnr"] for r in res["results"]]
    assert "close" in fnrs and "mid" in fnrs and "far" not in fnrs
    assert res["total"] == 2
    # default sort = ascending distance; each card carries distance_km.
    assert fnrs == ["close", "mid"]
    assert res["results"][0]["distance_km"] < res["results"][1]["distance_km"]


def test_tight_radius_excludes_the_19km_neighbor() -> None:
    svc, token = _svc([_doc("close", "4840"), _doc("mid", "4865")])
    res = svc.search_companies(
        token, SearchFilters(near=NearFilter(place="Vöcklabruck", radius_km=5))
    )
    assert [r["fnr"] for r in res["results"]] == ["close"]  # 19 km neighbor excluded at 5 km


def test_near_by_postal_code_anchor() -> None:
    svc, token = _svc([_doc("a", "4810"), _doc("b", "1010")])
    res = svc.search_companies(
        token, SearchFilters(near=NearFilter(postal_code="4810", radius_km=25))
    )
    assert [r["fnr"] for r in res["results"]] == ["a"]
    assert res["applied_filters"]["near"]["postal_code"] == "4810"


def test_ambiguous_place_is_bad_request_with_candidates() -> None:
    svc, token = _svc([_doc("a", "4810")])
    with pytest.raises(BadRequest) as exc:
        svc.search_companies(token, SearchFilters(near=NearFilter(place="Neudorf")))
    assert exc.value.code == "bad_request"
    assert "PLZ" in exc.value.message  # lists candidate PLZs


def test_unknown_place_is_bad_request() -> None:
    svc, token = _svc([_doc("a", "4810")])
    with pytest.raises(BadRequest):
        svc.search_companies(token, SearchFilters(near=NearFilter(place="Definitelynotatown")))


def test_exactly_one_anchor_required() -> None:
    svc, token = _svc([_doc("a", "4810")])
    with pytest.raises(BadRequest):
        svc.search_companies(token, SearchFilters(near=NearFilter()))  # neither
    with pytest.raises(BadRequest):
        svc.search_companies(
            token,
            SearchFilters(near=NearFilter(place="Gmunden", postal_code="4810")),  # both
        )


def test_distance_sort_requires_near() -> None:
    from fbl_core_at.models import Sort

    svc, token = _svc([_doc("a", "4810")])
    with pytest.raises(BadRequest):
        svc.search_companies(token, SearchFilters(), Sort(field="distance"))
