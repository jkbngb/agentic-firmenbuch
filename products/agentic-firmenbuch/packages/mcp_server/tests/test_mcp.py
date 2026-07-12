"""MCP tool + auth tests over an in-memory 10_presentation (§8.9 DoD)."""

from __future__ import annotations

from typing import Any

import pytest

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import RAW_CONTAINER, InMemoryBlobStore, InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService, NotFound, RateLimited, Unauthorized, build_app

PRESENTED = "10_presentation"


def _presented(
    fnr: str,
    *,
    name: str,
    bundesland: str,
    gkl: str,
    bilanzsumme: float | None,
    has_guv_latest: bool,
    equity_ratio: float,
    profile: str,
    legal_form: str = "GES",
    data_version: int = 1,
    manager_name: str | None = None,
    founded_year: int | None = None,
    geschaeftszweig: str | None = None,
    branch: dict[str, Any] | None = None,
    industry: dict[str, Any] | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    street: str | None = None,
) -> dict[str, Any]:
    doc = {
        "id": fnr,
        "fnr": fnr,
        "schema_version": "1.0",
        "identity": {"fnr": fnr, "name": name, "legal_form": legal_form, "status": "active"},
        "location": {
            "bundesland": bundesland,
            "postal_code": postal_code,
            "city": city,
            "street": street,
        },
        "company": {
            "last_filing_year": 2024,
            "founded_year": founded_year,
            "description": geschaeftszweig,
        },
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
    if branch is not None:
        doc["branch"] = branch
    if industry is not None:
        doc["industry"] = industry
    return doc


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
    # Pro token: these tests validate the underlying service (full data + every tool). The
    # Free-plan feature gates (flattened card, monthly cap, Pro-only tools) are covered
    # separately in test_plans.py; a Pro account bypasses them so this file stays about behavior.
    cosmos = _store()
    token = signup("user@example.test", cosmos, tier="pro").token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    return svc, token


def test_tool_calls_are_metered_and_get_my_usage_reports_them() -> None:
    cosmos = _store()
    token = signup("user@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))

    svc.search_companies(token)  # 1 unit
    svc.get_company_details(token, "030435h")  # 2 units
    svc.get_company_details(token, "093450b")  # 2 units

    usage = svc.get_my_usage(token, "today")
    # 3 data calls + the get_my_usage call itself (0 units) = 4 calls, 5 units
    assert usage["totals"]["calls"] == 4
    assert usage["totals"]["compute_units"] == 1 + 2 + 2 + 0
    assert usage["by_tool"]["get_company_details"] == {"calls": 2, "compute_units": 4}
    assert usage["tier"] == "free"
    # privacy: no e-mail leaks through the usage view
    assert "example.test" not in str(usage)


def test_industry_block_served_on_detail_and_card() -> None:
    # v2 (#34): codes come ONLY from stored classification, never a serve-time guess.
    # Without a stored block: free text + null codes. Legacy v1 `branch` docs translate
    # into the v2 shape; the `branch` key itself is no longer served.
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        PRESENTED,
        _presented(
            "011111a",
            name="Hausverwaltung GmbH",
            bundesland="W",
            gkl="K",
            bilanzsumme=1.0,
            has_guv_latest=False,
            equity_ratio=0.5,
            profile="stable",
            geschaeftszweig="Immobilienverwaltung",
        ),
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "033333c",
            name="Beratung Alt GmbH",
            bundesland="W",
            gkl="K",
            bilanzsumme=1.0,
            has_guv_latest=False,
            equity_ratio=0.5,
            profile="stable",
            geschaeftszweig="Unternehmensberatung",
            # legacy v1 doc: stored `branch` block from the first grind
            branch={
                "geschaeftszweig": "Unternehmensberatung",
                "oenace": {"section": "N", "division": "70", "group": "70.2", "label": "x"},
                "nace_rev21_group": "70.2",
                "source": "llm",
                "code_2008": "70.2",
            },
        ),
    )
    token = signup("u@example.test", cosmos, tier="pro").token  # asserts premium card fields
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))

    # no stored classification -> honest gap: text served, codes null, no `branch` key
    res = svc.get_company_details(token, "011111a")["result"]
    assert "branch" not in res
    ind = res["industry"]
    assert ind["geschaeftszweig"] == "Immobilienverwaltung"
    assert ind["oenace"] is None and ind["nace"] is None

    # legacy v1 doc -> translated into the v2 shape (labels all levels, symmetric nace)
    ind2 = svc.get_company_details(token, "033333c")["result"]["industry"]
    assert ind2["oenace"]["group"] == "70.2"
    assert ind2["oenace"]["group_label_de"] == "Unternehmensberatung"
    assert ind2["oenace"]["division_label_de"] and ind2["oenace"]["section_label_en"]
    assert ind2["nace"]["group"] == "70.2" and ind2["nace"]["version"] == "NACE_REV_2.1"
    assert ind2["nace"]["group_label"] == ind2["oenace"]["group_label_en"]

    card = svc.search_companies(token, SearchFilters(name="Hausverwaltung"))["results"][0]
    # industry_section is null here → omitted from the card entirely under the response diet (T8).
    assert card["geschaeftszweig"] == "Immobilienverwaltung"
    assert card.get("industry_section") is None
    # no stored classification -> the division/group fields are null too, not guessed (#35);
    # omitted from the card under the response diet (T8).
    assert card.get("oenace_division") is None and card.get("oenace_group") is None
    card2 = svc.search_companies(token, SearchFilters(name="Beratung Alt"))["results"][0]
    assert card2["industry_section"] == "N"
    # division/group + German labels served on the card, symmetric with the oenace_* filters (#35)
    assert card2["oenace_division"] == "70" and card2["oenace_group"] == "70.2"
    assert card2["oenace_division_label"] and card2["oenace_group_label"] == "Unternehmensberatung"


def test_search_filters_by_branch_and_location() -> None:
    # Issue #19: filter search_companies by ÖNACE branch + PLZ/Ort + Geschäftszweig directly.
    cosmos = InMemoryCosmosStore()
    common: dict[str, Any] = dict(
        bundesland="W",
        gkl="K",
        bilanzsumme=1.0,
        has_guv_latest=False,
        equity_ratio=0.5,
        profile="stable",
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "011111a",
            name="Immo Wien GmbH",
            geschaeftszweig="Immobilienverwaltung",
            postal_code="1010",
            city="Wien",
            branch={"oenace": {"section": "M", "division": "68", "group": "68.3"}},
            **common,
        ),
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "022222b",
            name="Bau Graz GmbH",
            geschaeftszweig="Baumeistergewerbe",
            postal_code="8010",
            city="Graz",
            branch={"oenace": {"section": "F", "division": "41", "group": "41.2"}},
            **common,
        ),
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "044444d",
            name="Berater Neu GmbH",
            geschaeftszweig="Unternehmensberatung",
            postal_code="4020",
            city="Linz",
            # v2 doc: stored `industry` block from the re-grind
            industry={
                "geschaeftszweig": "Unternehmensberatung",
                "oenace": {"section": "N", "division": "70", "group": "70.2"},
            },
            **common,
        ),
    )
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    token = signup("u@example.test", cosmos).token

    def fnrs(**flt: Any) -> set[str]:
        return {r["fnr"] for r in svc.search_companies(token, SearchFilters(**flt))["results"]}

    # legacy v1 `branch` docs still match (transition) …
    assert fnrs(oenace_section="M") == {"011111a"}
    assert fnrs(oenace_group="68.3") == {"011111a"}
    assert fnrs(oenace_division="41") == {"022222b"}
    # … and v2 `industry` docs match the same filters
    assert fnrs(oenace_section="N") == {"044444d"}
    assert fnrs(oenace_group="70.2") == {"044444d"}
    assert fnrs(geschaeftszweig="immobilien") == {"011111a"}
    assert fnrs(postal_code="10") == {"011111a"}  # PLZ prefix (all 10xx)
    assert fnrs(postal_code="8010") == {"022222b"}  # exact
    assert fnrs(city="graz") == {"022222b"}
    assert fnrs(oenace_section="M", postal_code="80") == set()  # combined, no match


def test_search_matches_both_oenace_vintages() -> None:
    """The zero-result bug: filtering by ÖNACE 2008 division 45 (motor-vehicle trade) returned
    nothing because the served codes are ÖNACE 2025 (car trade = 46/47). Filters now match BOTH
    vintages — via the stored oenace_2008 twin (post-regrind) OR the code_2008 prefix (before it)
    — so either code resolves the same companies and the card is self-explanatory."""
    cosmos = InMemoryCosmosStore()
    common: dict[str, Any] = dict(
        bundesland="W",
        gkl="K",
        bilanzsumme=1.0,
        has_guv_latest=False,
        equity_ratio=0.5,
        profile="stable",
    )
    # post-regrind doc: full oenace_2008 twin present (car retail: 2008 45.11 → 2025 47.8)
    cosmos.upsert(
        PRESENTED,
        _presented(
            "055555e",
            name="Autohaus Wien GmbH",
            geschaeftszweig="Handel mit Kraftwagen",
            industry={
                "geschaeftszweig": "Handel mit Kraftwagen",
                "oenace": {"section": "G", "division": "47", "group": "47.8"},
                "oenace_2008": {
                    "section": "G",
                    "division": "45",
                    "group": "45.1",
                    "class": "45.11",
                },
                "code_2008": "45.11",
            },
            **common,
        ),
    )
    # pre-regrind doc: only the 2025 block + code_2008 string (2008 45.31 → 2025 46.7)
    cosmos.upsert(
        PRESENTED,
        _presented(
            "066666f",
            name="Autoteile Graz GmbH",
            geschaeftszweig="Handel mit Kraftfahrzeugteilen",
            industry={
                "geschaeftszweig": "Handel mit Kraftfahrzeugteilen",
                "oenace": {"section": "G", "division": "46", "group": "46.7"},
                "code_2008": "45.31",
            },
            **common,
        ),
    )
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    # Pro token: the precise oenace_division/group (both vintages) is a premium card field — the
    # free tier keeps only industry_section (plans.FREE_CARD_KEEP). Filtering works for every
    # plan; the card *detail* assertions below need full access.
    token = signup("v@example.test", cosmos, tier="pro").token

    def fnrs(**flt: Any) -> set[str]:
        return {r["fnr"] for r in svc.search_companies(token, SearchFilters(**flt))["results"]}

    # ÖNACE 2025 divisions still resolve exactly …
    assert fnrs(oenace_division="47") == {"055555e"}
    assert fnrs(oenace_division="46") == {"066666f"}
    # … and the ÖNACE 2008 division 45 now resolves BOTH — via the twin block and the code prefix
    assert fnrs(oenace_division="45") == {"055555e", "066666f"}
    # groups match per vintage too (2008 45.11 → group 45.1; 45.31 → group 45.3)
    assert fnrs(oenace_group="45.1") == {"055555e"}
    assert fnrs(oenace_group="45.3") == {"066666f"}
    assert fnrs(oenace_group="47.8") == {"055555e"}
    # the card carries the 2008 twin so a "division 45" hit explains itself (also pre-regrind)
    card = svc.search_companies(token, SearchFilters(name="Autoteile"))["results"][0]
    assert card["oenace_division"] == "46" and card["oenace_division_2008"] == "45"
    assert card["oenace_group_2008"] == "45.3"


def test_list_sectors_exposes_divisions_per_vintage() -> None:
    """list_sectors is the discovery surface for the oenace_* filters: it shows which divisions
    exist in EACH vintage, so a caller sees 2025 has no 45 but 2008 does (→ use either)."""
    cosmos = InMemoryCosmosStore()
    common: dict[str, Any] = dict(
        bundesland="W",
        gkl="K",
        bilanzsumme=1.0,
        has_guv_latest=False,
        equity_ratio=0.5,
        profile="stable",
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "077777g",
            name="Autohaus GmbH",
            geschaeftszweig="Handel mit Kraftwagen",
            industry={
                "oenace": {"section": "G", "division": "47", "group": "47.8"},
                "code_2008": "45.11",
            },
            **common,
        ),
    )
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    token = signup("w@example.test", cosmos).token
    divs = svc.list_sectors(token)["result"]["oenace_divisions"]
    assert "47" in divs["2025"]["divisions"] and divs["2025"]["divisions"]["47"]["count"] == 1
    assert "45" in divs["2008"]["divisions"]  # the 2008 division exists even pre-regrind
    assert divs["2008"]["divisions"]["45"]["label_de"]  # official label attached
    assert "45" not in divs["2025"]["divisions"]  # … and is absent from 2025


def test_search_card_exposes_seat_address() -> None:
    # Issue #28: PLZ/Ort/Straße direkt auf der Karte — kein get_company_details-Umweg mehr.
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        PRESENTED,
        _presented(
            "022222b",
            name="Bau Graz GmbH",
            bundesland="St",
            gkl="K",
            bilanzsumme=1.0,
            has_guv_latest=False,
            equity_ratio=0.5,
            profile="stable",
            postal_code="8010",
            city="Graz",
            street="Hauptplatz 1",
        ),
    )
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    token = signup("u@example.test", cosmos, tier="pro").token  # street is a Pro card field

    card = svc.search_companies(token, SearchFilters(name="Bau Graz"))["results"][0]
    assert card["postal_code"] == "8010"
    assert card["city"] == "Graz"
    assert card["street"] == "Hauptplatz 1"


def test_get_my_usage_requires_auth() -> None:
    svc, _token = _svc()
    with pytest.raises(Unauthorized):
        svc.get_my_usage("not-a-real-token", "today")


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


def test_financial_institution_flag_and_caveat() -> None:
    # A regulated bank (the Volksbank NÖ AG case) and an ordinary GmbH share a store.
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        PRESENTED,
        _presented(
            "012345a",
            name="Volksbank Niederösterreich AG",
            legal_form="AG",
            bundesland="N",
            gkl="K",
            bilanzsumme=0.0,
            has_guv_latest=False,
            equity_ratio=0.0,
            profile="stable",
        ),
    )
    cosmos.upsert(
        PRESENTED,
        _presented(
            "067890b",
            name="Tischlerei Huber GmbH",
            bundesland="W",
            gkl="K",
            bilanzsumme=500000.0,
            has_guv_latest=True,
            equity_ratio=0.4,
            profile="growing",
        ),
    )
    token = signup("u@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))

    bank = svc.get_company_details(token, "012345a")["result"]
    assert bank["financial_institution"]["kind"] == "bank"
    assert "BWG" in bank["financial_institution"]["caveat"]

    plain = svc.get_company_details(token, "067890b")["result"]
    assert "financial_institution" not in plain  # ordinary GmbH: no FI block

    # The flag also rides on the compact search card.
    cards = {c["fnr"]: c for c in svc.search_companies(token)["results"]}
    assert cards["012345a"]["is_financial_institution"] is True
    assert cards["067890b"]["is_financial_institution"] is False


def test_register_directory_flag_overrides_name_heuristic() -> None:
    # Issue #15: a bank the NAME heuristic MISSES (no "bank" keyword) is still flagged because
    # it's in the OeNB register set (00_directories) — source="register", not "heuristic".
    from fbl_core_at.directories import DIRECTORIES_CONTAINER

    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        PRESENTED,
        _presented(
            "012345a",
            name="BAWAG Group AG",
            legal_form="AG",
            bundesland="W",
            gkl="K",
            bilanzsumme=0.0,
            has_guv_latest=False,
            equity_ratio=0.0,
            profile="stable",
        ),
    )
    cosmos.upsert(
        DIRECTORIES_CONTAINER,
        {"id": "012345a", "fnr": "012345a", "kind": "bank", "active": True},
    )
    token = signup("u@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    fi = svc.get_company_details(token, "012345a")["result"]["financial_institution"]
    assert fi["kind"] == "bank" and fi["source"] == "register"  # register-backed, not a name guess
    assert "BWG" in fi["caveat"]
    # An inactive register row must NOT flag (licence lost). The served directory is TTL-cached
    # (T3), so a same-process mutation isn't reflected until the entry expires (≤15 min) or the
    # process restarts — invalidate here to exercise the post-refresh state deterministically.
    cosmos.upsert(
        DIRECTORIES_CONTAINER,
        {"id": "012345a", "fnr": "012345a", "kind": "bank", "active": False},
    )
    from fbl_core_at import directories as _directories

    _directories._FI_CACHE.clear()
    plain = svc.get_company_details(token, "012345a")["result"]
    assert "financial_institution" not in plain


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
    token = signup("ops@example.test", cosmos, tier="pro").token  # get_full_record is Pro-only
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
    # No blob configured (the default _svc) → metadata only, no download link.
    assert doc["download"] is None
    with pytest.raises(NotFound):
        svc.get_document(token, "missing-key")


def test_get_company_details_stamps_document_ref() -> None:
    svc, token = _svc()
    result = svc.get_company_details(token, "030435h")["result"]
    # Each filing carries a resolvable ref the agent can hand straight to get_document.
    assert result["filings"][0]["document_ref"] == "030435h:2024-12-31"


def _fi_store_with_pdf() -> tuple[InMemoryCosmosStore, InMemoryBlobStore]:
    """A served Volksbank (FI) + its official PDF Jahresabschluss in 90-raw (manifest + bytes)."""
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        PRESENTED,
        _presented(
            "012345f",
            name="Volksbank Niederösterreich AG",
            bundesland="N",
            gkl="G",
            bilanzsumme=0.0,
            has_guv_latest=False,
            equity_ratio=0.0,
            profile="stable",
            legal_form="AG",
        ),
    )
    blob = InMemoryBlobStore()
    stichtag = "2024-12-31"
    blob_path = f"012345f/{stichtag}/012345f_{stichtag}_abc1234567_jb.pdf"
    blob.put_bytes(RAW_CONTAINER, blob_path, b"%PDF-1.7 official bank filing")
    blob.put_json(
        RAW_CONTAINER,
        f"012345f/{stichtag}/_manifest.json",
        {
            "artifacts": [
                {
                    "blob_path": f"{RAW_CONTAINER}/{blob_path}",
                    "doc_key": "VBNOE-PDF",
                    "dateiendung": "pdf",
                    "content_type": "application/pdf",
                    "bytes": 29,
                    "eingereicht": "2025-04-30",
                    "dokumentart": {"code": "JA", "text": "Jahresabschluss"},
                }
            ]
        },
    )
    return cosmos, blob


def test_get_document_returns_sas_download_for_fi_pdf() -> None:
    cosmos, blob = _fi_store_with_pdf()
    token = signup("user@example.test", cosmos, tier="pro").token  # get_document is Pro-only
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000), blob)

    result = svc.get_document(token, "012345f:2024-12-31")["result"]
    # DoD: FI flag + caveat + a working (here: faked) download link to the official PDF.
    assert result["financial_institution"]["kind"] == "bank"
    assert "BWG" in result["financial_institution"]["caveat"]
    assert result["document"]["dateiendung"] == "pdf"
    dl = result["download"]
    assert dl is not None
    # The signed URL targets the exact PDF blob and carries an expiry.
    assert dl["url"].startswith(
        "memory://90-raw/012345f/2024-12-31/012345f_2024-12-31_abc1234567_jb.pdf?"
    )
    assert f"se={dl['expires_at']}" in dl["url"]
    assert dl["expires_in_seconds"] > 0


def test_get_document_resolves_bare_fnr_to_latest_filing() -> None:
    cosmos, blob = _fi_store_with_pdf()
    token = signup("user@example.test", cosmos, tier="pro").token  # get_document is Pro-only
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000), blob)
    result = svc.get_document(token, "012345f")["result"]
    assert result["stichtag"] == "2024-12-31"
    assert result["download"] is not None


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
    assert set(cat["tiers"]) == {
        "search_companies",
        "get_company_details",
        "get_full_record",
        "list_events",
        "get_event_stats",
    }
    assert "has_guv_latest" in cat["tiers"]["search_companies"]["fields"]
    assert cat["codes"]["bundesland"]["W"] == "Wien"
    assert cat["reference_url"].endswith("/felder.html")
    assert any("summary card" in r for r in cat["availability_rules"])
    # ÖNACE 2025 division catalog (T7): ~87 entries with German labels, sourced from the tree.
    divisions = cat["codes"]["oenace_divisions"]
    assert len(divisions) >= 80
    by_code = {d["division"]: d["label_de"] for d in divisions}
    assert "68" in by_code and "Grundstücks- und Wohnungswesen" in by_code["68"]
    assert all(d["label_de"] for d in divisions)  # no empty labels


def test_describe_fields_requires_auth() -> None:
    svc, _ = _svc()
    with pytest.raises(Unauthorized):
        svc.describe_fields("not-a-real-token")


def test_build_app_registers_tools() -> None:
    app = build_app(_store(), Settings())
    assert app.name == "firmenbuch-live"


def test_every_tool_declares_title_and_readonly_hint() -> None:
    """Anthropic Connectors Directory hard gate: every tool must carry a human-readable
    ``title`` and ``readOnlyHint: true`` (all our tools are read-only). Missing annotations
    are an automatic rejection, so assert them here so a new tool can't ship without them."""
    app = build_app(_store(), Settings())
    tools = app._tool_manager.list_tools()
    assert tools, "no tools registered"
    for tool in tools:
        assert tool.title, f"tool {tool.name!r} is missing a human-readable title"
        assert tool.annotations is not None, f"tool {tool.name!r} has no annotations"
        assert tool.annotations.readOnlyHint is True, (
            f"tool {tool.name!r} must declare readOnlyHint=True"
        )
        assert len(tool.name) <= 64, f"tool name {tool.name!r} exceeds 64 chars"


def test_companies_without_bilanzsumme_are_not_dropped_from_sorted_lists() -> None:
    """#32/#24: the default sort (bilanzsumme desc) must NOT hide the ~40% of companies
    without a Bilanzsumme (banks/insurers etc.). They appear AFTER the ranked ones, by name,
    never interleaved, never dropped."""
    cosmos = InMemoryCosmosStore()
    common: dict[str, Any] = dict(
        bundesland="W", gkl="K", has_guv_latest=False, equity_ratio=0.5, profile="stable"
    )
    cosmos.upsert(PRESENTED, _presented("011a", name="Big GmbH", bilanzsumme=900000.0, **common))
    cosmos.upsert(PRESENTED, _presented("022b", name="Small GmbH", bilanzsumme=100000.0, **common))
    # a bank: no UGB Bilanzsumme at all
    cosmos.upsert(PRESENTED, _presented("033c", name="Zeta Bank AG", bilanzsumme=None, **common))
    cosmos.upsert(PRESENTED, _presented("044d", name="Alpha Bank AG", bilanzsumme=None, **common))
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    token = signup("u@example.test", cosmos).token

    res = svc.search_companies(token, SearchFilters())  # default sort = bilanzsumme desc
    assert res["total"] == 4  # nothing hidden
    order = [r["fnr"] for r in res["results"]]
    # ranked by bilanzsumme desc first, then the field-less banks by id (033c before 044d)
    assert order == ["011a", "022b", "033c", "044d"]

    # paging across the boundary keeps both buckets reachable
    p1 = svc.search_companies(token, SearchFilters(), page=1, page_size=2)
    p2 = svc.search_companies(token, SearchFilters(), page=2, page_size=2)
    assert [r["fnr"] for r in p1["results"]] == ["011a", "022b"]
    assert [r["fnr"] for r in p2["results"]] == ["033c", "044d"]
