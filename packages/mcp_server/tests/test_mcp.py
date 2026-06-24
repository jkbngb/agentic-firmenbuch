"""MCP tool + auth tests over an in-memory 10_presentation (§8.9 DoD)."""

from __future__ import annotations

from typing import Any

import pytest

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.models import SearchFilters
from fbl_core.storage import InMemoryCosmosStore
from fbl_mcp_server import McpService, NotFound, RateLimited, Unauthorized, build_app

PRESENTED = "10_presentation"


def _presented(
    fnr: str,
    *,
    name: str,
    bundesland: str,
    gkl: str,
    bilanzsumme: float,
    has_guv_latest: bool,
    equity_ratio: float,
    profile: str,
    legal_form: str = "GES",
    data_version: int = 1,
    manager_name: str | None = None,
    founded_year: int | None = None,
) -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "schema_version": "1.0",
        "identity": {"fnr": fnr, "name": name, "legal_form": legal_form, "status": "active"},
        "location": {"bundesland": bundesland},
        "company": {"last_filing_year": 2024, "founded_year": founded_year},
        "management": {"primary_manager": {"age": 55}, "primary_manager_name": manager_name},
        "size": {"gkl": gkl, "bilanzsumme_band": "small"},
        "financials": {
            "has_guv_latest": has_guv_latest,
            "has_guv": has_guv_latest,
            "latest": {"bilanzsumme": bilanzsumme, "revenue": 999.0 if has_guv_latest else None},
            "bilanz": {
                "bilanzsumme": {
                    "history": {"2024": bilanzsumme},
                    "source_codes": ["HGB_224_2"],
                    "paragraph_ref": "§224 Abs 2",
                }
            },
            "guv": (
                {"umsatzerloese": {"history": {"2024": 999.0}, "source_codes": []}}
                if has_guv_latest
                else {}
            ),
        },
        "ratios": {"equity_ratio": {"latest": equity_ratio, "history": {"2024": equity_ratio}}},
        "growth": {"profile": profile},
        "employees": {"latest": 10},
        "filings": [{"stichtag": "2024-12-31", "doc_key": f"{fnr}-KEY", "format": "xml"}],
        "provenance": {"data_version": data_version, "schema_version": "1.0"},
    }


def _store() -> InMemoryCosmosStore:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        PRESENTED,
        _presented(
            "030435h",
            name="Alpha GmbH",
            bundesland="W",
            gkl="K",
            bilanzsumme=500000.0,
            has_guv_latest=True,
            equity_ratio=0.4,
            profile="growing",
            manager_name="Klaus Mustermann",
            founded_year=2022,
        ),
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "093450b",
            name="Beta GmbH",
            bundesland="N",
            gkl="K",
            bilanzsumme=900000.0,
            has_guv_latest=False,
            equity_ratio=0.7,
            profile="stable",
        ),
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "032616s",
            name="Gamma GmbH",
            bundesland="W",
            gkl="M",
            bilanzsumme=8000000.0,
            has_guv_latest=True,
            equity_ratio=0.2,
            profile="fast_growing",
        ),
    )
    return cosmos


def _svc() -> tuple[McpService, str]:
    cosmos = _store()
    token = signup("user@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    return svc, token


def test_search_all() -> None:
    svc, token = _svc()
    resp = svc.search_companies(token)
    assert resp["total"] == 3
    assert {r["fnr"] for r in resp["results"]} == {"030435h", "093450b", "032616s"}


def test_search_has_guv_latest_filter() -> None:
    svc, token = _svc()
    resp = svc.search_companies(token, SearchFilters(has_guv_latest=True))
    assert resp["total"] == 2
    assert all(r["has_guv_latest"] for r in resp["results"])


def test_search_combined_filters() -> None:
    svc, token = _svc()
    resp = svc.search_companies(token, SearchFilters(bundesland="W", size_gkl="K"))
    assert resp["total"] == 1
    assert resp["results"][0]["fnr"] == "030435h"


def test_search_by_manager_name_and_card_exposes_it() -> None:
    # Officer-name search (public Firmenbuch data) — substring, case-insensitive.
    svc, token = _svc()
    resp = svc.search_companies(token, SearchFilters(manager_name="mustermann"))
    assert resp["total"] == 1
    card = resp["results"][0]
    assert card["fnr"] == "030435h"
    assert card["manager_name"] == "Klaus Mustermann"
    assert card["bilanzsumme_band"] == "small"  # honest size band on the card


def test_search_founded_year_range() -> None:
    svc, token = _svc()
    assert svc.search_companies(token, SearchFilters(founded_year_min=2022))["total"] == 1
    assert svc.search_companies(token, SearchFilters(founded_year_max=2000))["total"] == 0


def test_history_accepts_revenue_alias() -> None:
    # Eval gap: `revenue` (the card name) returned nothing; it maps to stored `umsatzerloese`.
    svc, token = _svc()
    hist = svc.get_company_history(token, "030435h", ["revenue"])
    assert hist["result"]["metrics"]["revenue"]["history"] == {"2024": 999.0}


def test_cohort_accepts_size_gkl_dimension_alias() -> None:
    svc, token = _svc()
    out = svc.get_cohort_summary(token, "size_gkl", "K")
    assert out["result"]["count"] == 2  # Alpha + Beta are gkl=K (Gamma is M)


def test_search_sort_and_paginate() -> None:
    svc, token = _svc()
    resp = svc.search_companies(token, page=1, page_size=2)
    assert resp["page_size"] == 2 and resp["total"] == 3
    # default sort: bilanzsumme desc -> Gamma (8M) first
    assert resp["results"][0]["fnr"] == "032616s"


def test_details_and_not_found() -> None:
    svc, token = _svc()
    detail = svc.get_company_details(token, "030435h")
    assert detail["result"]["identity"]["name"] == "Alpha GmbH"
    assert "meta" not in detail["result"]  # internal chain omitted
    with pytest.raises(NotFound):
        svc.get_company_details(token, "999999z")


def test_history() -> None:
    svc, token = _svc()
    hist = svc.get_company_history(token, "030435h", metrics=["bilanzsumme", "equity_ratio"])
    bs = hist["result"]["metrics"]["bilanzsumme"]
    assert bs["history"] == {"2024": 500000.0}
    # Part A.3: the official UGB code + §-ref are exposed per line item.
    assert bs["source_codes"] == ["HGB_224_2"]
    assert bs["ugb_paragraph"] == "§224 Abs 2"
    assert hist["result"]["metrics"]["equity_ratio"]["history"] == {"2024": 0.4}


def test_full_record_returns_superset_and_redacts_names() -> None:
    # Part B: get_full_record returns the derived superset (positions/passthrough/
    # completeness) with the internal chain stripped and officer names withheld by default.
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        "30_derived",
        {
            "id": "070707x",
            "fnr": "070707x",
            "identity": {"fnr": "070707x", "name": "Full GmbH"},
            "financials": {
                "positions": {"aktiva": {"latest": 100.0, "history": {"2024": 100.0}}},
                "passthrough": {},
                "completeness": {"2024": {"bilanz_items": 1}},
            },
            "management": {
                "primary_gf": {"first_name": "Max", "last_name": "X", "birth_year": 1970}
            },
            "meta": {"data_version": 3, "schema_version": "1.0"},
        },
    )
    token = signup("ops@example.test", cosmos).token
    svc = McpService(cosmos, Settings(expose_personal_data=False))
    full = svc.get_full_record(token, "070707x")["result"]
    assert full["financials"]["positions"]["aktiva"]["latest"] == 100.0  # full detail
    assert "completeness" in full["financials"]
    assert "meta" not in full  # internal hash chain stripped
    gf = full["management"]["primary_gf"]
    assert gf["birth_year"] == 1970  # year kept
    assert "first_name" not in gf and "last_name" not in gf  # names withheld (GDPR)
    with pytest.raises(Unauthorized):
        svc.get_full_record("bad-token", "070707x")


def test_list_sectors() -> None:
    svc, token = _svc()
    sectors = svc.list_sectors(token)["result"]
    assert sectors["size_classes"]["K"] == 2 and sectors["size_classes"]["M"] == 1
    assert sectors["legal_forms"]["GES"] == 3


def test_cohort_summary() -> None:
    svc, token = _svc()
    cohort = svc.get_cohort_summary(token, "gkl", "K")["result"]
    assert cohort["count"] == 2
    assert cohort["bilanzsumme_median"] == (500000.0 + 900000.0) / 2


def test_find_peers_same_band() -> None:
    svc, token = _svc()
    peers = svc.find_peers(token, "030435h")["result"]
    assert peers["gkl"] == "K"
    assert [p["fnr"] for p in peers["peers"]] == ["093450b"]  # only other K-band company


def test_get_document() -> None:
    svc, token = _svc()
    doc = svc.get_document(token, "030435h-KEY")["result"]
    assert doc["fnr"] == "030435h"
    with pytest.raises(NotFound):
        svc.get_document(token, "missing-key")


def test_unauthorized() -> None:
    svc, _ = _svc()
    with pytest.raises(Unauthorized):
        svc.search_companies("bad-token")


def test_rate_limited() -> None:
    cosmos = _store()
    token = signup("user@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=2, rate_limit_per_day=1000))
    svc.list_sectors(token)
    svc.list_sectors(token)
    with pytest.raises(RateLimited):
        svc.list_sectors(token)


def test_describe_fields_catalog() -> None:
    svc, token = _svc()
    cat = svc.describe_fields(token)
    # Lists every tier and the search card's exact fields; flags the summary/escalation rule.
    assert set(cat["tiers"]) == {"search_companies", "get_company_details", "get_full_record"}
    assert "has_guv_latest" in cat["tiers"]["search_companies"]["fields"]
    assert cat["codes"]["bundesland"]["W"] == "Wien"
    assert cat["reference_url"].endswith("/felder.html")
    assert any("summary card" in r for r in cat["availability_rules"])


def test_describe_fields_requires_auth() -> None:
    svc, _ = _svc()
    with pytest.raises(Unauthorized):
        svc.describe_fields("not-a-real-token")


def test_build_app_registers_tools() -> None:
    app = build_app(_store(), Settings())
    assert app.name == "firmenbuch-live"
