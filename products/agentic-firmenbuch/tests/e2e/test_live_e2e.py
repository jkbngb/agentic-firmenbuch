"""TRUE end-to-end smoke test: real API → every layer → an MCP query (§C).

Runs a SMALL, configurable set of real FNRs live through the whole pipeline:

    firmenbuch_client → 90_raw → 70_parsed → 50_consolidated → 30_derived
                      → 10_presented → MCP query against the result

It is **separate** from the fixture-based unit/integration tests and **guarded** behind
an env flag so it only runs on demand with a key present. It uses **in-memory** Blob/Cosmos
stores and a **tiny real pull** (a few FNRs) — it never provisions Azure or runs the full
backfill (see CLAUDE.md §D / the runbook).

Run it:

    FBL_E2E=1 FIRMENBUCH_API_KEY=... uv run pytest tests/e2e -q
    # optional: choose the companies (comma-separated FNRs)
    FBL_E2E=1 FBL_E2E_FNRS=030435h,030636d FIRMENBUCH_API_KEY=... uv run pytest tests/e2e -q

If `FBL_E2E` is unset (the default, incl. CI), the test is skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FNRS = "030435h,030636d"


def _load_env_value(key: str) -> str | None:
    """Read *key* from the process env, falling back to the repo-root .env (not committed)."""
    if os.environ.get(key):
        return os.environ[key]
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                if name.strip() == key:
                    return value.strip()
    return None


_ENABLED = os.environ.get("FBL_E2E") == "1"
_API_KEY = _load_env_value("FIRMENBUCH_API_KEY")

pytestmark = pytest.mark.skipif(
    not (_ENABLED and _API_KEY),
    reason="live E2E disabled — set FBL_E2E=1 and provide FIRMENBUCH_API_KEY to run",
)


def test_live_pipeline_through_all_layers() -> None:
    from fbl_auth import signup
    from fbl_core.config import Settings
    from fbl_core.storage import (
        PARSED_CONTAINER,
        RAW_CONTAINER,
        InMemoryBlobStore,
        InMemoryCosmosStore,
    )
    from fbl_firmenbuch_client import JustizOnlineClient
    from fbl_ingest import run_ingest
    from fbl_mcp_server import McpService
    from fbl_orchestration import PipelineContext, process_set
    from fbl_orchestration.loaders import parse_all
    from fbl_parse import parse_filing
    from fbl_registry import Registry

    api_url = _load_env_value("JUSTIZONLINE_API_URL") or (
        "https://justizonline.gv.at/jop/api/at.gv.justiz.fbw/ws"
    )
    raw_fnrs = os.environ.get("FBL_E2E_FNRS") or _DEFAULT_FNRS
    fnrs = [f.strip() for f in raw_fnrs.split(",") if f.strip()]
    assert _API_KEY is not None

    client = JustizOnlineClient(api_url, _API_KEY)
    cosmos = InMemoryCosmosStore()
    blob = InMemoryBlobStore()
    registry = Registry(cosmos)
    ctx = PipelineContext(
        blob=blob, cosmos=cosmos, source=client, registry=registry, current_year=2026
    )

    # Layer 99_registry: seed just the target FNRs (no full sweep).
    for fnr in fnrs:
        registry.ensure(fnr, source="e2e")

    # Layer 90_raw: download real artifacts.
    ingest_report = run_ingest(client, registry, blob, run_id="e2e", fnrs=fnrs)
    assert ingest_report.failures == 0, ingest_report.dead_letters
    raw_paths = blob.list_paths(RAW_CONTAINER)
    assert any(p.endswith(".xml") for p in raw_paths), "no raw XML downloaded"
    assert any("/master/" in p for p in raw_paths), "master auszug not archived"

    # Layer 70_parsed: a stored raw XML re-parses.
    xml_path = next(p for p in raw_paths if p.endswith(".xml"))
    raw_bytes = blob.get_bytes(RAW_CONTAINER, xml_path)
    assert raw_bytes is not None
    parsed = parse_filing(raw_bytes, run_id="e2e")
    assert parsed.parsed and parsed.bilanz.bilanzsumme is not None
    assert parse_all(blob, fnrs[0], run_id="e2e"), "parse_all found no filings"

    # Layers 50/30/10: consolidate → derive → present for the set.
    report = process_set(ctx, "e2e", fnrs)
    assert report.failures == 0, report.dead_letters
    assert report.processed == len(fnrs)

    for fnr in fnrs:
        assert cosmos.get("50_consolidated", fnr) is not None, f"50 missing for {fnr}"
        assert cosmos.get("30_derived", fnr) is not None, f"30 missing for {fnr}"
        presented = cosmos.get("10_presented", fnr)
        assert presented is not None, f"10 missing for {fnr}"
        assert presented["identity"]["status"] in ("active", "historical", "deleted")
        assert presented["financials"]["latest"].get("bilanzsumme") is not None
        # GDPR: the served body never carries an officer name.
        mgmt = presented.get("management") or {}
        assert mgmt.get("primary_manager_name") is None

    # Final layer: an MCP query against the served data.
    token = signup("e2e@example.test", cosmos).token
    svc = McpService(cosmos, Settings())
    search = svc.search_companies(token)
    assert search["total"] == len(fnrs)
    detail = svc.get_company_details(token, fnrs[0])
    assert detail["result"]["fnr"] == fnrs[0]
    history = svc.get_company_history(token, fnrs[0], metrics=["bilanzsumme"])
    assert "bilanzsumme" in history["result"]["metrics"]

    # Confirm the parsed projection also reached Blob 70-parsed is optional; the in-memory
    # run keeps parsed docs transient (process_set re-parses from 90-raw), so we assert the
    # raw + consolidated/derived/presented layers above. (PARSED_CONTAINER exists for the
    # Azure path.)
    assert PARSED_CONTAINER == "70-parsed"
