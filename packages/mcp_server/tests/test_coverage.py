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
