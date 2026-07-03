"""Change-feed-derived register events (issue #16).

The auszug tier returns no VOLLZ history, so events are derived by diffing the master snapshot
against the baseline captured at the previous consolidation. Covered here: first-run establishes a
baseline + emits nothing (no spurious flood), each change type emits the right event, the go-live
floor suppresses pre-2026-07-01 events, dedup across re-runs, and history accumulates.
"""

from __future__ import annotations

from fbl_consolidate import EVENTS_START, consolidate, derive_register_events, master_signature
from fbl_core_at.models import ConsolidatedCompany, Location, Manager, MasterData, Money

AFTER = "2026-07-15"  # after the go-live floor


def _master(
    *,
    name: str = "Alpha GmbH",
    legal_form: str = "GES",
    city: str = "Wien",
    capital: float = 35000.0,
    persons: list[Manager] | None = None,
) -> MasterData:
    return MasterData(
        fnr="030435h",
        name=name,
        legal_form=legal_form,
        location=Location(city=city, postal_code="1010", street="Ring 1"),
        stammkapital=Money(amount=capital),
        persons=persons if persons is not None else [Manager(last_name="Huber", role_label="GF")],
    )


def _consolidate(
    master: MasterData, prev: ConsolidatedCompany | None, today: str | None
) -> ConsolidatedCompany:
    # No filings: this exercises the master/event path in isolation.
    return consolidate("030435h", [], master, prev, today=today)


def test_first_run_establishes_baseline_and_emits_nothing() -> None:
    doc = _consolidate(_master(), None, AFTER)
    assert doc.events == []
    assert doc.event_baseline is not None
    assert doc.event_baseline["name"] == "Alpha GmbH"


def test_name_change_emits_event_with_previous_value() -> None:
    base = _consolidate(_master(name="Alpha GmbH"), None, AFTER)
    nxt = _consolidate(_master(name="Alpha Holding GmbH"), base, AFTER)
    assert [e.type for e in nxt.events] == ["name_change"]
    ev = nxt.events[0]
    assert ev.date == AFTER and ev.source == "change_feed_delta"
    assert ev.description == "vormals: Alpha GmbH"
    # baseline advanced to the new value
    assert nxt.event_baseline is not None and nxt.event_baseline["name"] == "Alpha Holding GmbH"


def test_seat_legalform_capital_management_changes() -> None:
    base = _consolidate(_master(), None, AFTER)
    changed = _master(
        legal_form="AG",
        city="Graz",
        capital=70000.0,
        persons=[Manager(last_name="Neu", role_label="GF")],
    )
    nxt = _consolidate(changed, base, AFTER)
    assert {e.type for e in nxt.events} == {
        "legal_form_change",
        "seat_change",
        "capital_change",
        "management_change",
    }


def test_go_live_floor_suppresses_events_before_start() -> None:
    base = _consolidate(_master(name="Alpha GmbH"), None, "2026-06-29")
    # A change observed BEFORE the floor: baseline advances, but no event is emitted.
    nxt = _consolidate(_master(name="Alpha Neu GmbH"), base, "2026-06-30")
    assert nxt.events == []
    assert nxt.event_baseline is not None and nxt.event_baseline["name"] == "Alpha Neu GmbH"
    assert EVENTS_START == "2026-07-01"


def test_backfill_today_none_derives_nothing_but_keeps_baseline() -> None:
    base = _consolidate(_master(name="Alpha GmbH"), None, AFTER)
    nxt = _consolidate(_master(name="Whatever GmbH"), base, None)
    assert nxt.events == []  # bulk backfill never derives
    assert nxt.event_baseline is not None and nxt.event_baseline["name"] == "Whatever GmbH"


def test_history_accumulates_and_dedups_across_reruns() -> None:
    base = _consolidate(_master(name="Alpha GmbH"), None, AFTER)
    after_rename = _consolidate(_master(name="Beta GmbH"), base, AFTER)
    assert len(after_rename.events) == 1
    # Re-run the SAME day with the SAME data: no change, no duplicate event, history preserved.
    rerun = _consolidate(_master(name="Beta GmbH"), after_rename, AFTER)
    assert len(rerun.events) == 1
    # A second, later change appends a new event on top of the kept history.
    later = _consolidate(_master(name="Gamma GmbH"), rerun, "2026-08-01")
    assert [e.type for e in later.events] == ["name_change", "name_change"]
    assert [e.date for e in later.events] == [AFTER, "2026-08-01"]


def test_master_signature_is_order_independent_for_signatories() -> None:
    a = master_signature(_master(persons=[Manager(last_name="A"), Manager(last_name="B")]))
    b = master_signature(_master(persons=[Manager(last_name="B"), Manager(last_name="A")]))
    assert a == b


def test_derive_helper_no_master_returns_empty() -> None:
    events, baseline = derive_register_events(None, None, today=AFTER)
    assert events == [] and baseline is None
