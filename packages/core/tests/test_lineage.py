"""Lineage / hashing tests (Technische Spezifikation §7).

The DoD for `core`: ``content_hash`` is stable across runs for identical input —
the basis of idempotency / skip-unchanged.
"""

from __future__ import annotations

from fbl_core.lineage import (
    content_hash,
    hash_bytes,
    lineage_ref,
    new_doc_id,
    now_utc_z,
    stamp,
)
from fbl_core.models.meta import Meta


def _doc() -> dict[str, object]:
    return {
        "fnr": "093450b",
        "bilanz": {"bilanzsumme": 23492979.69, "eigenkapital": 6393383.95},
        "_meta": {
            "doc_id": "fixed-id",
            "content_hash": None,
            "timestamps": {"parsed_at": "2026-06-16T05:00:18Z"},
            "lineage": [],
            "inputs": [],
        },
    }


def test_content_hash_is_deterministic() -> None:
    assert content_hash(_doc()) == content_hash(_doc())


def test_content_hash_ignores_provenance_built_at() -> None:
    """`provenance.built_at` is set post-hash for the served record and must be excluded
    from the content hash — otherwise re-hashing the served doc moments later (or a few
    seconds later in CI) would produce a different hash for the same content."""
    a = {"foo": 1, "provenance": {"data_version": "v1", "built_at": "2026-01-01T00:00:00Z"}}
    b = {"foo": 1, "provenance": {"data_version": "v1", "built_at": "2099-12-31T23:59:59Z"}}
    assert content_hash(a) == content_hash(b)
    # other provenance fields still matter (this is identity, not blanket ignore)
    c = {"foo": 1, "provenance": {"data_version": "v2", "built_at": "2026-01-01T00:00:00Z"}}
    assert content_hash(a) != content_hash(c)


def test_content_hash_ignores_volatile_meta() -> None:
    a = _doc()
    b = _doc()
    # Different doc_id, timestamps, lineage, prior hash — but identical content.
    b["_meta"] = {
        "doc_id": "another-id",
        "content_hash": "sha256:stale",
        "timestamps": {"parsed_at": "2099-01-01T00:00:00Z"},
        "lineage": [{"stage": "raw", "doc_id": "x"}],
        "inputs": [{"stage": "parsed", "doc_id": "y"}],
    }
    assert content_hash(a) == content_hash(b)


def test_content_hash_ignores_checks_supersedes_and_data_version() -> None:
    # §7: the WHOLE meta block is excluded — including checks, supersedes, and
    # data_version — so a rebuild that only bumps the version keeps the same hash.
    a = _doc()
    b = _doc()
    b["_meta"] = {
        "doc_id": "v2-id",
        "content_hash": None,
        "timestamps": {"parsed_at": "2026-06-16T05:00:18Z"},
        "lineage": [],
        "inputs": [],
        "checks": {"aktiva_equals_passiva": True, "prior_year_reconciled": False},
        "data_version": 7,
        "supersedes": {"stage": "consolidated", "doc_id": "v1-id"},
    }
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_with_data() -> None:
    a = _doc()
    b = _doc()
    b["bilanz"] = {"bilanzsumme": 1.0, "eigenkapital": 6393383.95}
    assert content_hash(a) != content_hash(b)


def test_content_hash_key_order_independent() -> None:
    a = {"a": 1, "b": 2, "_meta": {}}
    b = {"b": 2, "a": 1, "_meta": {}}
    assert content_hash(a) == content_hash(b)


def test_hash_prefix_format() -> None:
    assert content_hash(_doc()).startswith("sha256:")
    assert hash_bytes(b"hello").startswith("sha256:")
    assert hash_bytes(b"hello") == hash_bytes(b"hello")
    assert hash_bytes(b"hello") != hash_bytes(b"world")


def test_new_doc_id_unique() -> None:
    assert new_doc_id() != new_doc_id()


def test_stamp_sets_timestamp_and_hash() -> None:
    payload = _doc()
    meta = Meta(
        doc_id="fixed-id",
        entity_id="093450b/2025-12-31",
        stage="parsed",
        producer="parse@1.0.0",
        run_id="2026-06-16-daily-0003",
    )
    stamped = stamp(meta, payload, stage_time_key="parsed_at")
    assert "parsed_at" in stamped.timestamps
    assert stamped.content_hash is not None
    # Re-stamping identical content yields the same content hash.
    meta2 = Meta(
        doc_id="other-id",
        entity_id="093450b/2025-12-31",
        stage="parsed",
        producer="parse@1.0.0",
        run_id="different-run",
    )
    stamped2 = stamp(meta2, payload, stage_time_key="parsed_at")
    assert stamped.content_hash == stamped2.content_hash


def test_lineage_ref_carries_hash_and_stage() -> None:
    meta = Meta(
        doc_id="raw-id",
        entity_id="093450b/2025-12-31",
        stage="raw",
        producer="ingest@1.0.0",
        run_id="run",
        content_hash="sha256:abc",
        timestamps={"ingested_at": "2026-06-16T05:00:12Z"},
    )
    ref = lineage_ref(meta)
    assert ref.stage == "raw"
    assert ref.doc_id == "raw-id"
    assert ref.content_hash == "sha256:abc"
    assert ref.created_at == "2026-06-16T05:00:12Z"


def test_now_utc_z_format() -> None:
    ts = now_utc_z()
    assert ts.endswith("Z")
    assert "T" in ts
