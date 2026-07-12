"""T5 — per-tool telemetry: one event per call with all attributes; a true no-op when off.

These tests use the in-memory store, so RU is 0 (the fake doesn't report a request charge) but
every other attribute — tool, duration, session id, plan, result_total, zero_hit, filters_used
(NAMES only), page — must be present and correct. The privacy invariant (no filter *values* ever
leave the process) is asserted explicitly.
"""

from __future__ import annotations

from typing import Any

import pytest

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_core_at.models import SearchFilters
from fbl_mcp_server import McpService
from fbl_mcp_server import telemetry as tel


def _svc() -> tuple[McpService, str, InMemoryCosmosStore]:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(
        "10_presentation",
        {
            "id": "111a",
            "fnr": "111a",
            "identity": {
                "fnr": "111a",
                "name": "Novomatic AG",
                "legal_form": "GES",
                "status": "active",
            },
            "location": {"bundesland": "W"},
            "financials": {"latest": {"bilanzsumme": 5000.0}},
            "provenance": {"data_version": 1},
        },
    )
    token = signup("u@example.test", cosmos).token
    svc = McpService(cosmos, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    return svc, token, cosmos


@pytest.fixture
def sink() -> Any:
    events = tel.enable_test_sink()
    yield events
    tel.disable_test_sink()


def test_search_emits_one_event_with_all_attributes(sink: Any) -> None:
    svc, token, _ = _svc()
    svc.search_companies(token, SearchFilters(name="novomatic", bundesland="Wien"), page=1)

    assert len(sink) == 1
    ev = sink[0]
    assert ev["tool"] == "search_companies"
    assert ev["result_total"] == 1 and ev["zero_hit"] is False
    assert ev["page"] == 1
    assert isinstance(ev["duration_ms"], float) and ev["duration_ms"] >= 0
    assert ev["ru_total"] == 0.0  # in-memory store reports no request charge
    assert ev["plan"] is not None  # a plan was resolved and bound
    assert "mcp_session_id" in ev  # present (None without an HTTP session, which is fine)
    # filters_used carries NAMES only, sorted, and never the values "novomatic"/"Wien".
    assert ev["filters_used"] == "bundesland,name"
    assert "novomatic" not in str(ev) and "Wien" not in str(ev)


def test_zero_hit_flag_true_when_no_results(sink: Any) -> None:
    svc, token, _ = _svc()
    svc.search_companies(token, SearchFilters(name="does-not-exist-xyz"))
    ev = sink[-1]
    assert ev["result_total"] == 0 and ev["zero_hit"] is True
    assert ev["filters_used"] == "name"


def test_status_default_not_counted_as_active_filter(sink: Any) -> None:
    svc, token, _ = _svc()
    # status defaults to "all"; it must not appear in filters_used, but an explicit name does.
    svc.search_companies(token, SearchFilters(name="novomatic", status="all"))
    assert sink[-1]["filters_used"] == "name"


def test_other_tool_emits_event(sink: Any) -> None:
    svc, token, _ = _svc()
    svc.describe_fields(token)
    assert sink[-1]["tool"] == "describe_fields"


def test_noop_when_disabled() -> None:
    # No sink, telemetry not enabled → tool_span must not record or raise.
    tel.disable_test_sink()
    tel._STATE.enabled = False
    svc, token, _ = _svc()
    result = svc.search_companies(token, SearchFilters(name="novomatic"))
    assert result["total"] == 1  # the tool still works
    assert tel._STATE.recent is None  # nothing captured
