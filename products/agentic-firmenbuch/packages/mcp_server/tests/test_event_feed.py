"""Tests for the cross-company event feed (list_events / get_event_stats) + the pipeline
flattening (present.event_records)."""

from __future__ import annotations

from typing import Any

from fbl_auth import signup
from fbl_core.config import Settings
from fbl_core.storage import InMemoryCosmosStore
from fbl_mcp_server import McpService
from fbl_mcp_server.service import get_event_stats, list_events
from fbl_present import event_records

EVENTS = "10_events"


def _ev(fnr: str, date: str, etype: str, **extra: Any) -> dict[str, Any]:
    d = {
        "id": f"{fnr}:{date}:{etype}",
        "fnr": fnr,
        "name": f"{fnr} GmbH",
        "date": date,
        "type": etype,
        "description": "x",
        "bundesland": "W",
        "legal_form": "GES",
        "oenace_section": "C",
        "oenace_division": "28",
        "source": "change_feed_delta",
    }
    d.update(extra)
    return d


def _seed() -> InMemoryCosmosStore:
    c = InMemoryCosmosStore()
    c.upsert(
        EVENTS,
        _ev(
            "111a",
            "2026-07-02",
            "management_change",
            bundesland="W",
            managers_added=["GESCHÄFTSFÜHRER Max Muster"],
        ),
    )
    c.upsert(
        EVENTS,
        _ev(
            "222b",
            "2026-07-05",
            "capital_change",
            bundesland="N",
            capital_from=35000.0,
            capital_to=100000.0,
        ),
    )
    c.upsert(EVENTS, _ev("333c", "2026-07-08", "management_change", bundesland="W"))
    c.upsert(EVENTS, _ev("444d", "2026-06-15", "name_change", bundesland="W"))  # before window
    return c


def test_list_events_newest_first_and_window() -> None:
    c = _seed()
    out = list_events(c, since="2026-07-01")
    dates = [e["date"] for e in out["events"]]
    assert dates == ["2026-07-08", "2026-07-05", "2026-07-02"]  # newest first, June excluded
    assert out["total"] == 3


def test_list_events_filter_by_type_and_bundesland() -> None:
    c = _seed()
    out = list_events(c, since="2026-07-01", types=["management_change"], bundesland="Wien")
    assert {e["fnr"] for e in out["events"]} == {"111a", "333c"}
    # capital detail is surfaced structured
    cap = list_events(c, since="2026-07-01", types=["capital_change"])["events"][0]
    assert cap["capital_from"] == 35000.0 and cap["capital_to"] == 100000.0


def test_list_events_watchlist_and_managers() -> None:
    c = _seed()
    out = list_events(c, since="2026-07-01", fnrs=["111a"])
    assert len(out["events"]) == 1
    assert out["events"][0]["managers_added"] == ["GESCHÄFTSFÜHRER Max Muster"]
    assert out["events"][0]["legal_form"] == "GmbH"  # code labelled at serve


def test_get_event_stats_counts() -> None:
    c = _seed()
    stats = get_event_stats(c, since="2026-07-01")
    assert stats["total"] == 3
    assert stats["by_type"]["management_change"] == 2
    assert stats["by_bundesland"]["W"] == 2 and stats["by_bundesland"]["N"] == 1


def test_list_events_is_pro_gated() -> None:
    c = _seed()
    free = signup("free@example.test", c).token  # default tier = free
    pro = signup("pro@example.test", c, tier="pro").token
    svc = McpService(c, Settings(rate_limit_per_min=1000, rate_limit_per_day=10000))
    gated = svc.list_events(free, since="2026-07-01")
    assert gated.get("upgrade_required") is True and gated.get("tool") == "list_events"
    ok = svc.list_events(pro, since="2026-07-01")
    assert "events" in ok and ok["total"] == 3


def test_event_records_flatten_from_presented_doc() -> None:
    presented = {
        "fnr": "555e",
        "identity": {"name": "Delta GmbH", "legal_form": "GES"},
        "location": {"bundesland": "St"},
        "industry": {"oenace": {"section": "F", "division": "41"}},
        "events": [
            {
                "date": "2026-07-03",
                "type": "capital_change",
                "description": "35000.0 → 200000.0",
                "capital_from": 35000.0,
                "capital_to": 200000.0,
            },
            {
                "date": "2026-07-03",
                "type": "seat_change",
                "description": "neue Anschrift: 8010 Graz",
            },
        ],
    }
    recs = event_records(presented)
    assert len(recs) == 2
    r = {x["type"]: x for x in recs}
    assert r["capital_change"]["id"] == "555e:2026-07-03:capital_change"
    assert r["capital_change"]["name"] == "Delta GmbH"
    assert r["capital_change"]["bundesland"] == "St"
    assert r["capital_change"]["oenace_section"] == "F"
    assert r["capital_change"]["capital_to"] == 200000.0
    assert r["seat_change"]["oenace_division"] == "41"


def test_event_records_empty_when_no_events() -> None:
    assert event_records({"fnr": "1a", "events": []}) == []
    assert event_records({"fnr": "1a"}) == []
