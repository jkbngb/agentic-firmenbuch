"""T6 — zero-hit relaxation: when a multi-filter search returns nothing, the response tells the
caller which single filter to drop (with a nearest-achievable hint for numeric ranges), so the
LLM adjusts THAT filter instead of retrying blind combinations."""

from __future__ import annotations

from typing import Any

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService

PRESENTED = "10_presentation"


def _doc(
    fnr: str, *, name: str, bundesland: str, bilanzsumme: float | None, plz: str
) -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": name, "legal_form": "GES", "status": "active"},
        "location": {"bundesland": bundesland, "postal_code": plz},
        "financials": {"latest": {"bilanzsumme": bilanzsumme}},
        "provenance": {"data_version": 1},
    }


def _svc() -> tuple[McpService, str]:
    cosmos = InMemoryCosmosStore()
    # Wien companies with mid Bilanzsumme; a Tirol one; none matches "Wien + Bilanzsumme>=10M".
    cosmos.upsert(
        PRESENTED,
        _doc("1a", name="Alpha GmbH", bundesland="W", bilanzsumme=2_000_000.0, plz="1010"),
    )
    cosmos.upsert(
        PRESENTED, _doc("2b", name="Beta GmbH", bundesland="W", bilanzsumme=4_000_000.0, plz="1020")
    )
    cosmos.upsert(
        PRESENTED,
        _doc("3c", name="Gamma GmbH", bundesland="T", bilanzsumme=50_000_000.0, plz="6020"),
    )
    token = signup("u@example.test", cosmos).token
    return McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000)), token


def test_zero_hits_reports_each_droppable_filter() -> None:
    svc, token = _svc()
    # Wien AND Bilanzsumme >= 10M -> 0 hits. Dropping bundesland -> Tirol's 50M matches (1);
    # dropping the Bilanzsumme range -> the two Wien companies match (2).
    resp = svc.search_companies(
        token, SearchFilters(bundesland="Wien", bilanzsumme_min=10_000_000.0)
    )
    assert resp["total"] == 0
    relax = resp["relaxations"]
    assert relax is not None
    by = {r["dropped"]: r for r in relax}
    assert by["bundesland"]["total"] == 1
    assert by["bilanzsumme_range"]["total"] == 2
    # Most-permissive first.
    assert [r["dropped"] for r in relax] == sorted(by, key=lambda d: by[d]["total"], reverse=True)
    # The range unit carries a nearest-achievable hint from the OTHER-filters (Wien) result set.
    assert "nearest achievable bilanzsumme_range" in by["bilanzsumme_range"]["suggestion"]
    assert "2.0M" in by["bilanzsumme_range"]["suggestion"]  # min over the two Wien companies
    assert "4.0M" in by["bilanzsumme_range"]["suggestion"]  # max


def test_min_max_pair_is_one_unit() -> None:
    svc, token = _svc()
    resp = svc.search_companies(
        token,
        SearchFilters(
            bundesland="Wien", bilanzsumme_min=10_000_000.0, bilanzsumme_max=20_000_000.0
        ),
    )
    dropped = {r["dropped"] for r in resp["relaxations"]}
    # Both bounds collapse into a single "bilanzsumme_range" unit, never two entries.
    assert "bilanzsumme_range" in dropped
    assert "bilanzsumme_min" not in dropped and "bilanzsumme_max" not in dropped


def test_no_relaxations_with_single_filter() -> None:
    svc, token = _svc()
    # One active filter that matches nothing -> nothing to single out, so no relaxations block.
    resp = svc.search_companies(token, SearchFilters(name="does-not-exist-zzz"))
    assert resp["total"] == 0
    assert resp.get("relaxations") is None


def test_no_relaxations_when_hits_exist() -> None:
    svc, token = _svc()
    resp = svc.search_companies(
        token, SearchFilters(bundesland="Wien", bilanzsumme_min=1_000_000.0)
    )
    assert resp["total"] == 2
    assert resp.get("relaxations") is None
