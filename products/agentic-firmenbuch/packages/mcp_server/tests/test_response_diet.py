"""T8 (response diet) + T9 (applied_filters echo).

The search card must shed null fields entirely (token saving, no info loss), keep the two
non-null bool flags, drop the ÖNACE-2008 twin when it equals the 2025 code but keep it when the
vintages differ, and the response must echo the NORMALIZED filters actually applied.
"""

from __future__ import annotations

from typing import Any

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService

PRESENTED = "10_presentation"


def _svc_with(doc: dict[str, Any]) -> tuple[McpService, str]:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(PRESENTED, doc)
    token = signup("u@example.test", cosmos, tier="pro").token
    return McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000)), token


def _base_doc(fnr: str, **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": "Test GmbH", "legal_form": "GES", "status": "active"},
        "location": {"bundesland": "W", "postal_code": "1010"},  # no street/city
        "financials": {"latest": {"bilanzsumme": 1000.0}},  # no revenue
        "provenance": {"data_version": 1},
    }
    doc.update(over)
    return doc


def test_card_sheds_null_fields() -> None:
    svc, token = _svc_with(_base_doc("1a"))
    card = svc.search_companies(token, SearchFilters(name="Test"))["results"][0]
    # Present, non-null keys survive; null ones (street, revenue_latest, equity_ratio_latest, …)
    # are omitted rather than serialized as null.
    assert card["fnr"] == "1a" and card["bilanzsumme_latest"] == 1000.0
    assert "street" not in card
    assert "revenue_latest" not in card
    assert "manager_name" not in card
    # The two bool flags default False (not None) → they always serialize.
    assert card["has_guv_latest"] is False
    assert card["is_financial_institution"] is False


def test_oenace_2008_twin_dropped_when_equal_kept_when_different() -> None:
    # Same code in both vintages → twin omitted.
    same = _base_doc(
        "2b",
        industry={
            "geschaeftszweig": "Beratung",
            "oenace": {"division": "70", "group": "70.2", "division_label_de": "x", "group_label_de": "y"},
            "oenace_2008": {"division": "70", "group": "70.2", "division_label_de": "x", "group_label_de": "y"},
        },
    )
    svc, token = _svc_with(same)
    card = svc.search_companies(token, SearchFilters(name="Test"))["results"][0]
    assert card["oenace_division"] == "70"
    assert "oenace_division_2008" not in card and "oenace_group_2008" not in card

    # Different vintages (Kfz-Handel: 45 in 2008, 47 in 2025) → twin kept.
    diff = _base_doc(
        "3c",
        industry={
            "geschaeftszweig": "Kfz",
            "oenace": {"division": "47", "group": "47.3", "division_label_de": "Handel", "group_label_de": "g25"},
            "oenace_2008": {"division": "45", "group": "45.3", "division_label_de": "Kfz", "group_label_de": "g08"},
        },
    )
    svc2, token2 = _svc_with(diff)
    card2 = svc2.search_companies(token2, SearchFilters(name="Test"))["results"][0]
    assert card2["oenace_division_2008"] == "45" and card2["oenace_group_2008"] == "45.3"


def test_applied_filters_echoes_normalized_values() -> None:
    svc, token = _svc_with(_base_doc("4d"))
    resp = svc.search_companies(
        token, SearchFilters(bundesland="Wien", legal_form="GmbH", name="Test"), page_size=25
    )
    applied = resp["applied_filters"]
    assert applied["bundesland"] == "W"  # "Wien" normalized to the stored code
    assert applied["legal_form"] == "GE*"  # GmbH family → STARTSWITH GE prefix
    assert applied["name"] == "Test"
    assert applied["page_size"] == 25


def test_applied_filters_absent_for_unfiltered_search() -> None:
    svc, token = _svc_with(_base_doc("5e"))
    resp = svc.search_companies(token, SearchFilters())
    assert "applied_filters" not in resp  # nothing active → omitted under the diet


def test_page_size_clamped_in_applied_filters() -> None:
    svc, token = _svc_with(_base_doc("6f"))
    resp = svc.search_companies(token, SearchFilters(name="Test"), page_size=9999)
    assert resp["applied_filters"]["page_size"] == 100  # MAX_PAGE_SIZE
