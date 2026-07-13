"""T4 — the Cosmos paging path issues the *minimum* number of queries.

A counting fake store that behaves like real Cosmos (``SELECT VALUE COUNT(1)`` → int, SQL
honored enough to slice the two buckets) proves:
  * a full ranked page   → 2 Cosmos queries (total COUNT + bucket-A page), NO ranked COUNT;
  * a boundary/stitch page → 3 (… + bucket-B top-up), still NO ranked COUNT;
  * a deep page past the ranked bucket → 4 (the ranked COUNT reappears only here).
It also checks the two-bucket stitch order and the ``has_more`` flag.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from fbl_core_at.models import SearchFilters, Sort
from fbl_mcp_server.service.search import search_companies

_OFFSET_LIMIT = re.compile(r"OFFSET (\d+) LIMIT (\d+)")


def _doc(fnr: str, bilanzsumme: float | None) -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "identity": {"fnr": fnr, "name": f"Firma {fnr}", "legal_form": "GES", "status": "active"},
        "financials": {"latest": {"bilanzsumme": bilanzsumme}},
        "provenance": {"data_version": 1},
    }


class _CountingCosmos:
    """Fake that mimics Cosmos closely enough for the paging arithmetic: COUNT → int, ORDER BY
    ignored (the lists are pre-ordered by the test), OFFSET/LIMIT applied, buckets split by the
    IS_DEFINED predicate the pager builds. Counts every ``query`` call."""

    def __init__(self, ranked: list[dict[str, Any]], rest: list[dict[str, Any]]) -> None:
        self._ranked = ranked  # docs WITH the sort field, in sort order
        self._rest = rest  # docs WITHOUT it, in id order
        self.queries: list[str] = []

    def query(
        self, container: str, sql: str, params: list[dict[str, Any]] | None = None
    ) -> Iterator[Any]:  # yields ints for COUNT (like the real store), else docs
        self.queries.append(sql)
        is_rest = "NOT IS_DEFINED(" in sql
        is_ranked = "IS_DEFINED(" in sql and not is_rest
        if sql.startswith("SELECT VALUE COUNT(1)"):
            if is_ranked:
                yield from iter([len(self._ranked)])  # ranked-bucket COUNT
            else:
                yield from iter([len(self._ranked) + len(self._rest)])  # total COUNT
            return
        pool = self._ranked if is_ranked else self._rest if is_rest else self._ranked + self._rest
        m = _OFFSET_LIMIT.search(sql)
        if m:
            off, lim = int(m.group(1)), int(m.group(2))
            yield from pool[off : off + lim]
        else:
            yield from pool

    def iter_all(self, container: str) -> Iterator[dict[str, Any]]:
        yield from ()  # empty 00_directories

    def query_by_field(
        self, container: str, field: str, value: Any
    ) -> Iterator[dict[str, Any]]:  # pragma: no cover
        yield from ()

    def get(self, container: str, fnr: str) -> None:  # pragma: no cover
        return None

    def upsert(self, container: str, doc: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError("read-only")


def _n_page_queries(store: _CountingCosmos) -> int:
    """Queries excluding the directory read (which is cached/empty here)."""
    return len(store.queries)


def test_full_ranked_page_two_queries_no_ranked_count() -> None:
    ranked = [_doc(f"r{i:02d}", 1000.0 - i) for i in range(10)]
    store = _CountingCosmos(ranked, rest=[])
    resp = search_companies(store, SearchFilters(), Sort(field="bilanzsumme"), page=1, page_size=5)

    assert [c.fnr for c in resp.results] == ["r00", "r01", "r02", "r03", "r04"]
    assert resp.total == 10 and resp.has_more is True
    assert _n_page_queries(store) == 2  # total COUNT + bucket-A page; NO ranked COUNT
    assert not any("VALUE COUNT(1)" in q and "IS_DEFINED(" in q for q in store.queries)


def test_boundary_stitch_three_queries_no_ranked_count() -> None:
    ranked = [_doc(f"r{i:02d}", 1000.0 - i) for i in range(3)]  # 3 ranked
    rest = [_doc(f"z{i:02d}", None) for i in range(4)]  # 4 field-less
    store = _CountingCosmos(ranked, rest)
    resp = search_companies(store, SearchFilters(), Sort(field="bilanzsumme"), page=1, page_size=5)

    # ranked (by value) then rest (by id), stitched across the boundary, nothing dropped.
    assert [c.fnr for c in resp.results] == ["r00", "r01", "r02", "z00", "z01"]
    assert resp.total == 7 and resp.has_more is True
    assert _n_page_queries(store) == 3  # total COUNT + bucket-A + bucket-B; still NO ranked COUNT
    assert not any("VALUE COUNT(1)" in q and "IS_DEFINED(" in q for q in store.queries)


def test_deep_page_past_ranked_bucket_pays_for_ranked_count() -> None:
    ranked = [_doc(f"r{i:02d}", 1000.0 - i) for i in range(3)]  # 3 ranked
    rest = [_doc(f"z{i:02d}", None) for i in range(10)]  # plenty of field-less
    store = _CountingCosmos(ranked, rest)
    # page 2 of size 5: start=5, fully past the 3 ranked docs → into bucket B at offset 5-3=2.
    resp = search_companies(store, SearchFilters(), Sort(field="bilanzsumme"), page=2, page_size=5)

    assert [c.fnr for c in resp.results] == ["z02", "z03", "z04", "z05", "z06"]
    assert resp.total == 13 and resp.has_more is True
    # total COUNT + empty bucket-A probe + ranked COUNT (needed here) + bucket-B page = 4.
    assert _n_page_queries(store) == 4
    assert any("VALUE COUNT(1)" in q and "IS_DEFINED(" in q for q in store.queries)


def test_last_page_has_more_false() -> None:
    ranked = [_doc(f"r{i:02d}", 1000.0 - i) for i in range(6)]
    store = _CountingCosmos(ranked, rest=[])
    resp = search_companies(store, SearchFilters(), Sort(field="bilanzsumme"), page=2, page_size=5)
    assert [c.fnr for c in resp.results] == ["r05"]
    assert resp.total == 6 and resp.has_more is False  # 5 + 1 == 6
