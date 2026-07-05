"""Plan feature-gating tests: free card flattening, monthly cap, Pro-only gates,
guest expiry, and full-access plans (Stripe billing, Aufgabe 2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fbl_auth import signup
from fbl_auth.accounts import ACCOUNTS_CONTAINER
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService, plans

PRESENTED = "10_presentation"


def _doc(fnr: str, name: str) -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "schema_version": "1.0",
        "identity": {"fnr": fnr, "name": name, "legal_form": "GES", "status": "active"},
        "location": {"bundesland": "W", "postal_code": "1010", "city": "Wien", "street": "Ring 1"},
        "company": {"last_filing_year": 2024, "description": "Softwareentwicklung"},
        "management": {"primary_manager": {"age": 50}, "primary_manager_name": "Eva Muster"},
        "size": {"gkl": "K", "bilanzsumme_band": "small"},
        "financials": {
            "has_guv_latest": True,
            "has_guv": True,
            "latest": {"bilanzsumme": 500000.0, "revenue": 999.0},
            "bilanz": {"bilanzsumme": {"history": {"2024": 500000.0}, "source_codes": []}},
            "guv": {"umsatzerloese": {"history": {"2024": 999.0}, "source_codes": []}},
        },
        "ratios": {"equity_ratio": {"latest": 0.42, "history": {"2024": 0.42}}},
        "growth": {"profile": "growing"},
        "employees": {"latest": 10},
        "filings": [{"stichtag": "2024-12-31", "doc_key": f"{fnr}-KEY", "format": "xml"}],
        "industry": {
            "geschaeftszweig": "Softwareentwicklung",
            "oenace": {"section": "J", "division": "62", "group": "62.0"},
        },
        "provenance": {"data_version": 1, "schema_version": "1.0"},
    }


def _store() -> InMemoryCosmosStore:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(PRESENTED, _doc("030435h", "Alpha GmbH"))
    cosmos.upsert(PRESENTED, _doc("093450b", "Beta GmbH"))
    return cosmos


def _svc(cosmos: InMemoryCosmosStore, **overrides: Any) -> McpService:
    base = dict(rate_limit_per_min=1000, rate_limit_per_day=100000)
    base.update(overrides)
    return McpService(cosmos, Settings(**base))


# --- pure policy ---------------------------------------------------------------


def test_effective_plan_and_full_access() -> None:
    assert plans.effective_plan("free") == "free"
    assert plans.effective_plan("pro") == "pro"
    assert plans.effective_plan(None) == "free"
    assert plans.is_full_access("pro")
    assert plans.is_full_access("guest") and plans.is_full_access("legacy")
    assert not plans.is_full_access("free")


def test_guest_reverts_to_free_when_expired() -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert plans.effective_plan("guest", future) == "guest"  # still in trial
    assert plans.effective_plan("guest", past) == "free"  # expired -> free
    assert plans.effective_plan("guest", None) == "guest"  # open-ended guest (no expiry set)


def test_flatten_free_card_blanks_premium_fields() -> None:
    card = {
        "fnr": "1",
        "name": "X",
        "legal_form": "GmbH",
        "bundesland": "Wien",
        "postal_code": "1010",
        "city": "Wien",
        "industry_section": "J",
        "bilanzsumme_latest": 5.0,
        "equity_ratio_latest": 0.4,
        "revenue_latest": 9.0,
        "manager_name": "Eva",
        "oenace_division": "62",
        "street": "Ring 1",
    }
    flat = plans.flatten_free_card(card)
    assert flat["name"] == "X" and flat["bilanzsumme_latest"] == 5.0
    assert flat["industry_section"] == "J"
    assert flat["equity_ratio_latest"] is None and flat["revenue_latest"] is None
    assert flat["manager_name"] is None
    assert flat["oenace_division"] is None and flat["street"] is None


# --- service wiring ------------------------------------------------------------


def test_free_search_returns_flattened_card_with_plan_note() -> None:
    cosmos = _store()
    token = signup("free@example.test", cosmos).token  # default free
    resp = _svc(cosmos).search_companies(token, SearchFilters(name="Alpha"))
    card = resp["results"][0]
    assert card["name"] == "Alpha GmbH" and card["bilanzsumme_latest"] == 500000.0
    assert card["industry_section"] == "J"
    # premium fields blanked for free
    assert card["equity_ratio_latest"] is None and card["revenue_latest"] is None
    assert card["manager_name"] is None and card["oenace_division"] is None
    assert "plan_note" in resp


def test_pro_search_keeps_full_card() -> None:
    cosmos = _store()
    token = signup("pro@example.test", cosmos, tier="pro").token
    resp = _svc(cosmos).search_companies(token, SearchFilters(name="Alpha"))
    card = resp["results"][0]
    assert card["equity_ratio_latest"] == 0.42 and card["revenue_latest"] == 999.0
    assert card["manager_name"] == "Eva Muster" and card["oenace_division"] == "62"
    assert "plan_note" not in resp


def test_free_details_full_profile_until_monthly_cap() -> None:
    cosmos = _store()
    token = signup("free@example.test", cosmos).token
    svc = _svc(cosmos, free_details_per_month=2)
    # first two calls return the full profile
    d1 = svc.get_company_details(token, "030435h")
    d2 = svc.get_company_details(token, "093450b")
    assert d1["result"]["identity"]["name"] == "Alpha GmbH"
    assert "upgrade_required" not in d1 and "upgrade_required" not in d2
    # third call this month is gated
    d3 = svc.get_company_details(token, "030435h")
    assert d3.get("upgrade_required") is True
    assert d3["reason"] == "free_monthly_limit_reached"
    assert d3["upgrade_url"]


def test_pro_details_have_no_cap() -> None:
    cosmos = _store()
    token = signup("pro@example.test", cosmos, tier="pro").token
    svc = _svc(cosmos, free_details_per_month=1)
    for _ in range(5):
        out = svc.get_company_details(token, "030435h")
        assert "upgrade_required" not in out


def test_free_pro_only_tools_are_gated() -> None:
    cosmos = _store()
    token = signup("free@example.test", cosmos).token
    svc = _svc(cosmos)
    for call in (
        lambda: svc.find_peers(token, "030435h"),
        lambda: svc.get_cohort_summary(token, "gkl", "K"),
        lambda: svc.get_company_history(token, "030435h", ["bilanzsumme"]),
        lambda: svc.get_full_record(token, "030435h"),
        lambda: svc.get_document(token, "030435h-KEY"),
    ):
        out = call()
        assert out.get("upgrade_required") is True and out["reason"] == "pro_only"


def test_legacy_plan_has_full_access() -> None:
    cosmos = _store()
    token = signup("old@example.test", cosmos, tier="legacy").token
    svc = _svc(cosmos)
    assert "upgrade_required" not in svc.find_peers(token, "030435h")
    resp = svc.search_companies(token, SearchFilters(name="Alpha"))
    assert resp["results"][0]["equity_ratio_latest"] == 0.42  # not flattened


def test_guest_full_access_then_expires_to_free() -> None:
    cosmos = _store()
    rec = signup("guest@example.test", cosmos, tier="guest")
    token = rec.token
    svc = _svc(cosmos)
    # active guest: full access to a Pro-only tool
    assert "upgrade_required" not in svc.find_peers(token, "030435h")
    # expire the guest -> next call is gated like free
    acct = rec.account
    acct.plan_expires_at = (datetime.now(UTC) - timedelta(seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cosmos.upsert(ACCOUNTS_CONTAINER, acct.model_dump(mode="json"))
    assert svc.find_peers(token, "030435h").get("upgrade_required") is True
