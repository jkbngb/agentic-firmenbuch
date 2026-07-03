"""Singleton run lock — a lease doc in 99_registry (§15a.3).

Guarantees no two pipeline runs overlap: a second invocation finds the lease held
(and unexpired) and exits without doing work.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fbl_core.lineage import now_utc_z
from fbl_core.storage import CosmosStoreLike

RUN_LOCK_ID = "__runlock__"
REGISTRY_CONTAINER = "99_registry"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def acquire_run_lock(cosmos: CosmosStoreLike, run_id: str, *, ttl_sec: int = 14400) -> bool:
    """Try to acquire the run lock; True if acquired, False if a live lease is held."""
    existing = cosmos.get(REGISTRY_CONTAINER, RUN_LOCK_ID)
    if existing is not None:
        expires = _parse(existing.get("expires_at"))
        if expires is not None and expires > _now():
            return False  # a previous run is still going
    expires_at = (_now() + timedelta(seconds=ttl_sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cosmos.upsert(
        REGISTRY_CONTAINER,
        {
            "id": RUN_LOCK_ID,
            "fnr": RUN_LOCK_ID,
            "run_id": run_id,
            "acquired_at": now_utc_z(),
            "expires_at": expires_at,
        },
    )
    return True


def heartbeat_run_lock(cosmos: CosmosStoreLike, run_id: str, *, ttl_sec: int = 14400) -> bool:
    """Extend this run's lease by ``ttl_sec`` from now (§15a.3).

    A long backfill can outlive a fixed TTL; without renewal the lease would expire
    mid-run and a second invocation could overtake it. Call periodically during a run.
    Returns False if the lock is no longer held by this run (lost/overtaken) — the
    caller should treat that as a signal to abort rather than keep writing.
    """
    existing = cosmos.get(REGISTRY_CONTAINER, RUN_LOCK_ID)
    if existing is None or existing.get("run_id") != run_id:
        return False  # lost the lock — another run owns it now
    existing["expires_at"] = (_now() + timedelta(seconds=ttl_sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing["heartbeat_at"] = now_utc_z()
    cosmos.upsert(REGISTRY_CONTAINER, existing)
    return True


def release_run_lock(cosmos: CosmosStoreLike, run_id: str) -> None:
    """Release the lock if this run holds it (set it expired)."""
    existing = cosmos.get(REGISTRY_CONTAINER, RUN_LOCK_ID)
    if existing is not None and existing.get("run_id") == run_id:
        existing["expires_at"] = now_utc_z()  # immediately expired
        cosmos.upsert(REGISTRY_CONTAINER, existing)


@contextmanager
def run_lock(cosmos: CosmosStoreLike, run_id: str, *, ttl_sec: int = 14400) -> Iterator[bool]:
    """Context manager yielding whether the lock was acquired; releases on exit."""
    acquired = acquire_run_lock(cosmos, run_id, ttl_sec=ttl_sec)
    try:
        yield acquired
    finally:
        if acquired:
            release_run_lock(cosmos, run_id)
