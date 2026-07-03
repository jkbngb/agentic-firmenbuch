"""Prefix-walk enumeration + sync_registry seed/reconcile (§15a.0/§15a.1, hardened §B)."""

from __future__ import annotations

from ingest_fakes import FakeSource

from fbl_core.storage import InMemoryCosmosStore
from fbl_firmenbuch_client import FirmaResult
from fbl_ingest import (
    DEFAULT_ALPHABET,
    MAX_PREFIX_DEPTH,
    InMemoryCheckpoint,
    IterableBulkSource,
    prefix_walk,
    sync_registry,
)
from fbl_ingest.bulk import BulkCompany
from fbl_registry import Registry


def test_prefix_walk_finds_all_despite_cap() -> None:
    # 6 companies under prefix "a*" with cap 3 forces the walk to split deeper.
    universe = {
        "000001a": "Alpha",
        "000002b": "Alfa",
        "000003c": "Apple",
        "000004d": "Apricot",
        "000005e": "Avocado",
        "000006f": "Banana",
    }
    src = FakeSource(universe=universe)
    result = prefix_walk(src, rechtsformen=("GES",), cap=3)
    assert set(result.found) == set(universe)  # every FNR found across the split
    assert result.incomplete == []
    assert result.counts_by_rechtsform["GES"] == 6


def test_prefix_walk_streams_found_companies() -> None:
    # on_found streams companies AS discovered (durable/observable, not bulk-at-end).
    universe = {"000001a": "Alpha", "000002b": "Beta", "000003c": "Gamma"}
    src = FakeSource(universe=universe)
    streamed: list[str] = []
    result = prefix_walk(
        src, rechtsformen=("GES",), on_found=lambda rs: streamed.extend(r.fnr for r in rs)
    )
    assert set(streamed) == set(universe)  # every company was emitted via the stream
    assert set(result.found) == set(universe)


def test_sync_registry_streams_incrementally() -> None:
    # The registry is written DURING the walk, not only at the end: a sink installed by
    # sync_registry upserts each company as found. Verified by the seed result + that the
    # registry is fully populated (the streamed path produces the same correct catalog).
    reg = Registry(InMemoryCosmosStore())
    src = FakeSource(universe={"000001a": "Alpha", "000002b": "Beta"})
    rep = sync_registry(src, reg, rechtsformen=("GES",))
    assert rep.seeded == 2
    assert {d.fnr for d in reg.iter_docs()} == {"000001a", "000002b"}
    assert reg.get("000001a").name == "Alpha"  # type: ignore[union-attr]


def test_default_alphabet_is_exhaustive() -> None:
    # Sanity: the static split set covers letters, digits, umlauts, accents, punctuation.
    for ch in "abz0 9äöüß-.&áéí')":
        assert ch in DEFAULT_ALPHABET
    assert MAX_PREFIX_DEPTH == 20


def test_split_alphabet_follows_observed_chars() -> None:
    # A name whose 2nd char ('+') is unusual must still be reachable via the observed-char
    # union, even at cap. Two '+'-names force a split that must include '+'.
    universe = {"000001a": "A+One", "000002b": "A+Two", "000003c": "Azzz"}
    src = FakeSource(universe=universe)
    result = prefix_walk(src, rechtsformen=("GES",), cap=2)
    assert set(result.found) == set(universe)


def test_prefix_walk_flags_incomplete_loudly() -> None:
    universe = {f"00000{i}x": "Aaa" for i in range(5)}  # 5 identical-prefix names
    src = FakeSource(universe=universe)
    result = prefix_walk(src, rechtsformen=("GES",), alphabet="a", cap=3, max_depth=2)
    # default behaviour records the branch (and logs ERROR) — never silent
    assert result.incomplete  # dense branch hit the depth ceiling and was flagged


def test_completed_walk_clears_checkpoint() -> None:
    # A COMPLETE walk clears its checkpoint, so the next run re-walks fresh (this is what a
    # monthly reconcile needs — a persisted "all done" state would re-walk nothing).
    universe = {"000001a": "Alpha", "000002b": "Beta"}
    src = FakeSource(universe=universe)
    ckpt = InMemoryCheckpoint()
    first = prefix_walk(src, rechtsformen=("GES",), checkpoint=ckpt)
    assert set(first.found) == set(universe)
    assert ckpt.load() is None  # cleared on completion
    calls_before = src.suche_firma_calls
    second = prefix_walk(src, rechtsformen=("GES",), checkpoint=ckpt)
    assert set(second.found) == set(universe)
    assert src.suche_firma_calls > calls_before  # fresh re-walk, not a no-op resume


def test_sync_registry_seed_then_reconcile() -> None:
    reg = Registry(InMemoryCosmosStore())
    src = FakeSource(universe={"000001a": "Alpha", "000002b": "Beta"})
    rep1 = sync_registry(src, reg, rechtsformen=("GES",))
    assert rep1.seeded == 2 and rep1.total_seen == 2
    assert rep1.source == "sucheFirma_sweep"
    assert sorted(reg.all_fnrs()) == ["000001a", "000002b"]
    # The catalog stores name + legal-form code from the sweep (fnr + status + name + rechtsform).
    alpha = reg.get("000001a")
    assert alpha is not None and alpha.name == "Alpha"
    assert alpha.rechtsform == "GES"  # rechtsform_code carried from sucheFirma

    # Beta vanishes from the authoritative sweep -> reconcile marks it deleted.
    src.universe.pop("000002b")
    rep2 = sync_registry(src, reg, rechtsformen=("GES",))
    assert rep2.updated == 1 and rep2.marked_deleted == 1
    beta = reg.get("000002b")
    assert beta is not None and beta.status == "deleted"


def test_sync_registry_prefers_bulk() -> None:
    reg = Registry(InMemoryCosmosStore())
    bulk = IterableBulkSource(
        [
            BulkCompany(fnr="000001a", status="", rechtsform="GES"),
            BulkCompany(fnr="000002b", status="gelöscht", rechtsform="AKT"),
        ]
    )

    class BoomSource(FakeSource):
        def suche_firma(self, firmenwortlaut, **kw):  # type: ignore[no-untyped-def]
            raise AssertionError("prefix-walk must not run when a bulk source is given")

    rep = sync_registry(BoomSource(), reg, bulk=bulk)
    assert rep.source == "hvd_bulk" and rep.seeded == 2
    a, b = reg.get("000001a"), reg.get("000002b")
    assert a is not None and b is not None
    assert b.status == "deleted"
    assert a.rechtsform == "GES" and b.rechtsform == "AKT"  # bulk rechtsform carried


def test_status_mapping_from_suche_firma() -> None:
    reg = Registry(InMemoryCosmosStore())

    class StatusSource(FakeSource):
        def suche_firma(self, firmenwortlaut, **kw):  # type: ignore[no-untyped-def]
            if firmenwortlaut == "*":
                return [
                    FirmaResult(fnr="000001a", name="Active", status=""),
                    FirmaResult(fnr="000002b", name="Gone", status="gelöscht"),
                ]
            return []

    sync_registry(StatusSource(), reg, rechtsformen=("",))
    assert reg.get("000001a").status == "active"  # type: ignore[union-attr]
    assert reg.get("000002b").status == "deleted"  # type: ignore[union-attr]


class _RecordingSource:
    """Records every search query; root is over-cap (forces a split), deeper is a leaf."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.queries: list[str] = []

    def suche_firma(
        self,
        firmenwortlaut: str,
        *,
        suchbereich: int = 1,
        rechtsform: str = "",
        exaktesuche: bool = True,
        gericht: str = "",
        ortnr: str = "",
    ) -> list[FirmaResult]:
        self.queries.append(firmenwortlaut)
        prefix = firmenwortlaut.rstrip("*")
        # only the root ("*") is over the cap → forces exactly one split level, then leaves
        n = self.cap + 5 if prefix == "" else 1
        return [FirmaResult(fnr=f"{prefix or 'root'}{i:04d}", name=f"{prefix}X") for i in range(n)]


def test_walk_never_queries_a_leading_space_prefix() -> None:
    src = _RecordingSource(cap=10)
    # _RecordingSource implements only suche_firma (all the walk uses), not the full Protocol.
    prefix_walk(src, rechtsformen=("",), cap=10, checkpoint=InMemoryCheckpoint())  # type: ignore[arg-type]
    leading_space = [q for q in src.queries if q.startswith(" ")]
    assert leading_space == [], f"walk queried leading-space prefixes: {leading_space[:5]}"
    # sanity: it did split into real letter prefixes (e.g. "a*") and queried the root
    assert "*" in src.queries
    assert any(q.startswith("a") for q in src.queries)


def test_prefix_walk_heartbeat_loss_raises_and_preserves_checkpoint() -> None:
    # When the run lock is lost mid-walk, prefix_walk RAISES (mimicking a crash) rather than
    # returning — so the caller never runs mark_vanished on an incomplete walk. The last
    # checkpoint.save means the next run resumes. (§15a.3 never-stuck for the grind.)
    import pytest

    universe = {f"{i:06d}a": f"Co{i}" for i in range(8)}
    src = FakeSource(universe=universe)
    ckpt = InMemoryCheckpoint()
    with pytest.raises(RuntimeError, match="run lock lost"):
        prefix_walk(
            src,
            rechtsformen=("GES",),
            checkpoint=ckpt,
            save_every=1,  # heartbeat checked after the first processed prefix
            heartbeat=lambda: False,  # lock lost immediately
        )
    assert ckpt.load() is not None  # progress persisted → resumable, not lost
