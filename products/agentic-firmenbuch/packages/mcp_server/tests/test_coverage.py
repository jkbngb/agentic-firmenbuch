"""Coverage dashboard test (§11)."""

from __future__ import annotations

import pytest

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_mcp_server import McpService, Unauthorized

REGISTRY = "99_registry"
CONSOLIDATED = "50_consolidated"


def _registry_doc(fnr: str, status: str, formats: list[str]) -> dict[str, object]:
    return {
        "id": fnr,
        "fnr": fnr,
        "status": status,
        "known_filings": [
            {"doc_key": f"{fnr}-{i}", "format": fmt} for i, fmt in enumerate(formats)
        ],
    }


def test_coverage_counts() -> None:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(REGISTRY, _registry_doc("a", "active", ["legacy_finanzonline", "pdf"]))
    cosmos.upsert(REGISTRY, _registry_doc("b", "active", ["pdf"]))  # pdf-only
    cosmos.upsert(REGISTRY, _registry_doc("c", "deleted", []))  # no filings
    cosmos.upsert(REGISTRY, {"id": "__watermark__", "fnr": "__watermark__"})  # ignored
    token = signup("ops@example.test", cosmos).token
    svc = McpService(cosmos, Settings())

    cov = svc.get_coverage(token)["result"]
    assert cov["total_companies"] == 3
    assert cov["with_xml"] == 1
    assert cov["pdf_only"] == 1
    assert cov["no_filings"] == 1
    assert cov["filings_by_format"]["pdf"] == 2
    assert cov["filings_by_format"]["legacy_finanzonline"] == 1
    assert cov["companies_by_status"] == {"active": 2, "deleted": 1}


def test_coverage_parse_success_by_format_and_year() -> None:
    cosmos = InMemoryCosmosStore()
    # One company: a parsed jab40 (2024) and a failed jab40 (2025) + a parsed legacy (2024).
    cosmos.upsert(
        CONSOLIDATED,
        {
            "id": "a",
            "fnr": "a",
            "filings": [
                {"stichtag": "2024-12-31", "format": "jab40_semantic", "parsed": True},
                {"stichtag": "2025-12-31", "format": "jab40_semantic", "parsed": False},
                {"stichtag": "2024-12-31", "format": "legacy_finanzonline", "parsed": True},
            ],
        },
    )
    token = signup("ops@example.test", cosmos).token
    cov = McpService(cosmos, Settings()).get_coverage(token)["result"]

    by_fmt = cov["parse_success_by_format"]
    assert by_fmt["jab40_semantic"] == {"total": 2, "parsed": 1, "rate": 0.5}
    assert by_fmt["legacy_finanzonline"]["rate"] == 1.0
    by_year = cov["parse_success_by_year"]
    assert by_year["2024"]["rate"] == 1.0
    assert by_year["2025"] == {"total": 1, "parsed": 0, "rate": 0.0}


def test_coverage_requires_auth() -> None:
    svc = McpService(InMemoryCosmosStore(), Settings())
    with pytest.raises(Unauthorized):
        svc.get_coverage("bad-token")


def test_store_stats_persists_sectors_before_coverage() -> None:
    """store_stats writes the sectors aggregate (incl. ÖNACE divisions) in its own upsert first,
    so the discovery surface lands even if the heavy coverage pass is interrupted. Also guards the
    presented-count path that must not materialise every doc (the refresh-stats OOM)."""
    from fbl_mcp_server.service import store_stats
    from fbl_mcp_server.service.stats import STATS_ID, list_sectors

    presented = "10_presentation"
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(REGISTRY, _registry_doc("a", "active", ["legacy_finanzonline"]))
    cosmos.upsert(
        presented,
        {
            "id": "011111a",
            "fnr": "011111a",
            "identity": {"legal_form": "GES", "status": "active"},
            "size": {"gkl": "K"},
            "industry": {
                "oenace": {"division": "47"},
                "code_2008": "45.11",
            },
        },
    )
    stats = store_stats(cosmos, include_coverage=True)
    assert "45" in stats["sectors"]["oenace_divisions_2008"]
    assert "47" in stats["sectors"]["oenace_divisions_2025"]
    # the persisted __stats__ doc carries both sectors and coverage
    stored_doc = cosmos.get(presented, STATS_ID)
    assert stored_doc is not None
    stored = stored_doc["stats"]
    assert stored["sectors"]["oenace_divisions_2008"]["45"] == 1
    assert stored["coverage"]["total_companies"] == 1
    # and list_sectors serves the divisions with official labels from the stored doc
    divs = list_sectors(cosmos)["result"]["oenace_divisions"]
    assert divs["2008"]["divisions"]["45"]["label_de"]
