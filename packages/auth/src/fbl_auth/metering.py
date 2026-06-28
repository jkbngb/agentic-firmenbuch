"""Per-user usage metering (Erweiterungen-Spec §8).

A persistent, daily-rollup meter that sits **alongside** the rolling rate-limit
counters in :mod:`fbl_auth.accounts`. Where ``record_usage`` keeps a single
overwriting window on the account (for rate-limit enforcement), this module
writes an **append-only history**: one document per ``(key_hash, day_utc)`` in
the ``00_usage`` container, so we can answer "how much has this user consumed"
over any window.

Three things are recorded per call:

* ``calls`` — flat invocation count.
* ``compute_units`` — a weighted cost per tool (see :data:`COMPUTE_UNITS`), so a
  cheap static ``describe_fields`` and an expensive ``get_cohort_summary`` are
  not counted equally. This is the metric meant for display / fair-use.
* ``by_tool`` — the same two counters broken down per tool.

(Cosmos RU accounting — the third counter in the design — needs the
``x-ms-request-charge`` response header threaded through the storage layer; it
is a documented follow-up, not implemented here.)

Privacy: only the **token hash** is stored, never the e-mail. The
``key_hash → email`` mapping lives only in ``00_accounts`` and requires
owner-scope to join, so a ``00_usage`` document is GDPR-anonymous on its own.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from fbl_core.storage import CosmosStoreLike

from .accounts import Account

USAGE_CONTAINER = "00_usage"

# Weighted cost per tool (Erweiterungen-Spec §8.2). 1 unit ≈ ~5 Cosmos RU. Unknown tools default
# to 1 so a newly-added tool is always metered, never silently free.
COMPUTE_UNITS: dict[str, int] = {
    "describe_fields": 0,
    "list_sectors": 0,
    "get_coverage": 1,
    "get_document": 1,
    "search_companies": 1,
    "get_company_details": 2,
    "get_company_history": 3,
    "find_peers": 5,
    "get_cohort_summary": 5,
    "get_full_record": 5,
    "get_my_usage": 0,  # introspection is free
}
_DEFAULT_UNITS = 1


def compute_units_for(tool: str) -> int:
    """Weighted cost of one call to *tool* (Erweiterungen-Spec §8.2)."""
    return COMPUTE_UNITS.get(tool, _DEFAULT_UNITS)


class ToolStat(BaseModel):
    """Per-tool counters within one day."""

    calls: int = 0
    compute_units: int = 0


class DailyUsage(BaseModel):
    """One ``(key_hash, day_utc)`` rollup document in ``00_usage``."""

    id: str  # == f"u_{keyhash16}_{day}" (partition key is /id)
    kind: str = "daily_usage"
    key_hash: str  # full sha256:<hex> token hash (joins to 00_accounts)
    day_utc: str  # "YYYY-MM-DD"
    tier: str = "free"
    calls: int = 0
    compute_units: int = 0
    by_tool: dict[str, ToolStat] = Field(default_factory=dict)
    first_call_at: str | None = None
    last_call_at: str | None = None


def _short_hash(token_hash: str) -> str:
    """Stable 16-char id fragment from a ``sha256:<hex>`` token hash."""
    return token_hash.split(":", 1)[-1][:16]


def usage_doc_id(token_hash: str, day: str) -> str:
    """Deterministic ``00_usage`` document id for a (key, day)."""
    return f"u_{_short_hash(token_hash)}_{day}"


def record_metered_usage(
    account: Account, tool: str, cosmos: CosmosStoreLike, *, now: datetime | None = None
) -> DailyUsage:
    """Add one call to today's rollup for *account* and persist it.

    Read-modify-write of the daily document (the store has no atomic patch). At
    our scale concurrent same-key writes are rare and the worst case is a lost
    increment, never corruption; if that ever matters, move to a Cosmos patch.
    Never raises into the request path — a metering failure must not fail a tool
    call (the caller wraps this defensively, but we keep it cheap and total).
    """
    now = now or datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    units = compute_units_for(tool)
    doc_id = usage_doc_id(account.token_hash, day)

    existing = cosmos.get(USAGE_CONTAINER, doc_id)
    if existing is None:
        usage = DailyUsage(
            id=doc_id,
            key_hash=account.token_hash,
            day_utc=day,
            tier=account.tier,
            first_call_at=ts,
        )
    else:
        usage = DailyUsage.model_validate(existing)

    usage.calls += 1
    usage.compute_units += units
    stat = usage.by_tool.setdefault(tool, ToolStat())
    stat.calls += 1
    stat.compute_units += units
    usage.last_call_at = ts
    if usage.first_call_at is None:
        usage.first_call_at = ts
    usage.tier = account.tier  # keep the tier current if it changed

    cosmos.upsert(USAGE_CONTAINER, usage.model_dump(mode="json"))
    return usage


# ---- read side -----------------------------------------------------------------

_WINDOWS = ("today", "yesterday", "month_to_date", "last_30_days", "all")


def _window_days(window: str, now: datetime) -> set[str] | None:
    """The set of ``day_utc`` strings a window covers, or ``None`` for 'all'."""
    today = now.date()
    if window == "today":
        return {today.isoformat()}
    if window == "yesterday":
        return {(today - timedelta(days=1)).isoformat()}
    if window == "month_to_date":
        return {d.isoformat() for d in _date_range(date(today.year, today.month, 1), today)}
    if window == "last_30_days":
        return {d.isoformat() for d in _date_range(today - timedelta(days=29), today)}
    return None  # "all"


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def get_usage(
    cosmos: CosmosStoreLike,
    token_hash: str,
    *,
    window: str = "today",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate a user's usage over *window*.

    Returns a plain dict ready for the ``get_my_usage`` MCP tool. ``ru_consumed``
    is intentionally absent (internal-only). Reads at most ~365 small docs from
    the user's own partition set, filtered to the window in Python.
    """
    now = now or datetime.now(UTC)
    if window not in _WINDOWS:
        window = "today"
    days = _window_days(window, now)

    calls = 0
    units = 0
    by_tool: dict[str, ToolStat] = {}
    first_at: str | None = None
    last_at: str | None = None
    tier = "free"
    n_days = 0

    for raw in cosmos.query_by_field(USAGE_CONTAINER, "key_hash", token_hash):
        doc = DailyUsage.model_validate(raw)
        if days is not None and doc.day_utc not in days:
            continue
        n_days += 1
        calls += doc.calls
        units += doc.compute_units
        tier = doc.tier
        for tool, stat in doc.by_tool.items():
            agg = by_tool.setdefault(tool, ToolStat())
            agg.calls += stat.calls
            agg.compute_units += stat.compute_units
        if doc.first_call_at and (first_at is None or doc.first_call_at < first_at):
            first_at = doc.first_call_at
        if doc.last_call_at and (last_at is None or doc.last_call_at > last_at):
            last_at = doc.last_call_at

    return {
        "window": window,
        "key_label": f"key-…{_short_hash(token_hash)[-4:]}",
        "tier": tier,
        "days_with_activity": n_days,
        "totals": {"calls": calls, "compute_units": units},
        "by_tool": {
            tool: {"calls": s.calls, "compute_units": s.compute_units}
            for tool, s in sorted(by_tool.items(), key=lambda kv: -kv[1].compute_units)
        },
        "first_call_at": first_at,
        "last_call_at": last_at,
    }
