"""TTL cache for the served FI directory (T3): a hit must not re-read ``00_directories``,
a store swap must miss, and the entry must expire after the TTL."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from fbl_core_at import directories
from fbl_core_at.directories import DIRECTORIES_CONTAINER, load_fi_directory_cached


class _CountingStore:
    """Minimal CosmosStoreLike that records how often ``iter_all`` (the full directory read)
    is called, so the test can prove the cache elides the repeat reads."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.reads = 0

    def iter_all(self, container: str) -> Iterator[dict[str, Any]]:
        assert container == DIRECTORIES_CONTAINER
        self.reads += 1
        yield from self._rows

    # Unused by load_fi_directory, present only to satisfy the structural protocol at runtime.
    def get(self, container: str, fnr: str) -> None:  # pragma: no cover - not exercised
        return None

    def query(self, container: str, sql: str, params: Any = None) -> Iterator[dict[str, Any]]:
        yield from ()

    def upsert(self, container: str, doc: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError("read-only in this test")


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    directories._FI_CACHE.clear()
    yield
    directories._FI_CACHE.clear()


def test_second_call_hits_cache_no_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(directories.time, "monotonic", lambda: clock["t"])
    store = _CountingStore([{"fnr": "123456a", "active": True, "kind": "bank"}])

    first = load_fi_directory_cached(store)
    assert first == {"123456a": "bank"} and store.reads == 1

    clock["t"] += 100.0  # still inside the 900 s TTL
    second = load_fi_directory_cached(store)
    assert second == {"123456a": "bank"} and store.reads == 1  # no re-read
    assert second is first  # same cached object handed back


def test_expiry_after_ttl_triggers_reread(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(directories.time, "monotonic", lambda: clock["t"])
    store = _CountingStore([{"fnr": "1a", "active": True, "kind": "insurer"}])

    load_fi_directory_cached(store)
    assert store.reads == 1

    clock["t"] += directories._FI_TTL_SECONDS + 1.0  # past the TTL
    load_fi_directory_cached(store)
    assert store.reads == 2  # stale → re-read


def test_new_store_identity_is_a_cache_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(directories.time, "monotonic", lambda: 500.0)
    store_a = _CountingStore([{"fnr": "1a", "active": True, "kind": "bank"}])
    store_b = _CountingStore([{"fnr": "2b", "active": True, "kind": "insurer"}])

    assert load_fi_directory_cached(store_a) == {"1a": "bank"}
    # A different store object (e.g. a fresh in-memory store per test) must not see A's data.
    assert load_fi_directory_cached(store_b) == {"2b": "insurer"}
    assert store_a.reads == 1 and store_b.reads == 1
