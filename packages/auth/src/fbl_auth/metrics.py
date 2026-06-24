"""Lightweight, privacy-friendly owner analytics (Distribution §14.8).

Server-side **daily counters** in ``00_accounts`` (``kind="metric"``) — no cookies, no client
tracking, no PII. The owner reads them straight from Cosmos. Counters used:
``signups_verified`` (a verified email → key) and ``playground_queries`` (deterministic answers).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fbl_core.storage import CosmosStoreLike

from .accounts import ACCOUNTS_CONTAINER, hash_token


def bump_metric(cosmos: CosmosStoreLike, name: str, *, now: datetime | None = None) -> None:
    """Increment the daily counter for *name* (best-effort; never raises into the caller's flow)."""
    now = now or datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    key = hash_token(f"metric:{name}:{day}")
    doc = cosmos.get(ACCOUNTS_CONTAINER, key) or {
        "id": key,
        "token_hash": key,
        "kind": "metric",
        "metric": name,
        "day": day,
        "count": 0,
    }
    doc["count"] = int(doc.get("count", 0)) + 1
    cosmos.upsert(ACCOUNTS_CONTAINER, doc)


def read_metric(cosmos: CosmosStoreLike, name: str, day: str) -> int:
    """Read the counter for *name* on *day* (YYYY-MM-DD); 0 if none."""
    doc = cosmos.get(ACCOUNTS_CONTAINER, hash_token(f"metric:{name}:{day}"))
    return int(doc.get("count", 0)) if doc else 0
