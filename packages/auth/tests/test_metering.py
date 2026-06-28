"""Per-user usage metering (Erweiterungen-Spec §8)."""

from __future__ import annotations

from datetime import UTC, datetime

from fbl_auth.accounts import Account, hash_token
from fbl_auth.metering import (
    USAGE_CONTAINER,
    compute_units_for,
    get_usage,
    record_metered_usage,
    usage_doc_id,
)
from fbl_core.storage import InMemoryCosmosStore


def _account(token: str = "tok-abc") -> Account:
    h = hash_token(token)
    return Account(id=h, token_hash=h, email="user@example.com", tier="free")


def test_compute_units_weights() -> None:
    assert compute_units_for("describe_fields") == 0
    assert compute_units_for("search_companies") == 1
    assert compute_units_for("get_company_details") == 2
    assert compute_units_for("get_full_record") == 5
    # unknown tool defaults to 1 (always metered, never silently free)
    assert compute_units_for("some_new_tool") == 1


def test_record_accumulates_calls_and_units_per_day() -> None:
    cosmos = InMemoryCosmosStore()
    acct = _account()
    now = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)

    record_metered_usage(acct, "search_companies", cosmos, now=now)  # 1 unit
    record_metered_usage(acct, "get_company_details", cosmos, now=now)  # 2 units
    record_metered_usage(acct, "get_full_record", cosmos, now=now)  # 5 units
    record_metered_usage(acct, "describe_fields", cosmos, now=now)  # 0 units

    doc = cosmos.get(USAGE_CONTAINER, usage_doc_id(acct.token_hash, "2026-07-01"))
    assert doc is not None
    assert doc["calls"] == 4
    assert doc["compute_units"] == 1 + 2 + 5 + 0
    assert doc["by_tool"]["get_full_record"] == {"calls": 1, "compute_units": 5}
    assert doc["by_tool"]["describe_fields"] == {"calls": 1, "compute_units": 0}
    assert doc["key_hash"] == acct.token_hash  # the hash, never the e-mail
    assert "example.com" not in str(doc)  # no e-mail anywhere in the usage doc


def test_separate_doc_per_day() -> None:
    cosmos = InMemoryCosmosStore()
    acct = _account()
    record_metered_usage(acct, "search_companies", cosmos, now=datetime(2026, 7, 1, tzinfo=UTC))
    record_metered_usage(acct, "search_companies", cosmos, now=datetime(2026, 7, 2, tzinfo=UTC))
    assert cosmos.get(USAGE_CONTAINER, usage_doc_id(acct.token_hash, "2026-07-01")) is not None
    assert cosmos.get(USAGE_CONTAINER, usage_doc_id(acct.token_hash, "2026-07-02")) is not None


def test_get_usage_today_vs_all() -> None:
    cosmos = InMemoryCosmosStore()
    acct = _account()
    record_metered_usage(acct, "get_company_details", cosmos, now=datetime(2026, 7, 1, tzinfo=UTC))
    record_metered_usage(acct, "get_company_details", cosmos, now=datetime(2026, 7, 1, tzinfo=UTC))
    record_metered_usage(acct, "search_companies", cosmos, now=datetime(2026, 7, 15, tzinfo=UTC))

    ref = datetime(2026, 7, 15, tzinfo=UTC)
    today = get_usage(cosmos, acct.token_hash, window="today", now=ref)
    assert today["totals"] == {"calls": 1, "compute_units": 1}

    everything = get_usage(cosmos, acct.token_hash, window="all", now=ref)
    assert everything["totals"] == {"calls": 3, "compute_units": 2 + 2 + 1}
    # by_tool sorted by compute_units desc → get_company_details first (2 units)
    assert list(everything["by_tool"]) == ["get_company_details", "search_companies"]
    assert everything["key_label"].startswith("key-…")
    assert "ru_consumed" not in everything["totals"]  # RU is internal-only


def test_get_usage_isolates_per_key() -> None:
    cosmos = InMemoryCosmosStore()
    a = _account("tok-A")
    b = _account("tok-B")
    record_metered_usage(a, "get_full_record", cosmos, now=datetime(2026, 7, 1, tzinfo=UTC))
    record_metered_usage(b, "search_companies", cosmos, now=datetime(2026, 7, 1, tzinfo=UTC))
    ua = get_usage(cosmos, a.token_hash, window="all", now=datetime(2026, 7, 1, tzinfo=UTC))
    assert ua["totals"] == {"calls": 1, "compute_units": 5}  # only A's usage, not B's


def test_window_month_to_date_and_last_30() -> None:
    cosmos = InMemoryCosmosStore()
    acct = _account()
    # one call on Jun 28 (last month-ish), one on Jul 1, one on Jul 20
    record_metered_usage(acct, "search_companies", cosmos, now=datetime(2026, 6, 28, tzinfo=UTC))
    record_metered_usage(acct, "search_companies", cosmos, now=datetime(2026, 7, 1, tzinfo=UTC))
    record_metered_usage(acct, "search_companies", cosmos, now=datetime(2026, 7, 20, tzinfo=UTC))
    ref = datetime(2026, 7, 20, tzinfo=UTC)

    mtd = get_usage(cosmos, acct.token_hash, window="month_to_date", now=ref)
    assert mtd["totals"]["calls"] == 2  # Jul 1 + Jul 20, not Jun 28

    last30 = get_usage(cosmos, acct.token_hash, window="last_30_days", now=ref)
    assert last30["totals"]["calls"] == 3  # Jun 28 is within 30 days of Jul 20
